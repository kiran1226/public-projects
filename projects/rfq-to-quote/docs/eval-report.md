# Evaluation report

Generated 2026-11-17 by `rfq2quote eval` on 72 labelled RFQs (197 line items). LLM configurations use `claude-haiku-5-5` at `medium` effort.

| Configuration | What it is |
|---|---|
| `baseline` | Regular expressions for extraction (delivery location is not attempted), top search hit for matching. No LLM. |
| `llm-v1` | Claude extraction with a three-sentence prompt, Claude matching. |
| `llm-v2` | Claude extraction with the full rule prompt, Claude matching. |

The development set was visible while the prompts were written. The held-out set was written after the prompts were frozen. The harder set was added later: its labels were chosen in code and its texts were written by a model, Claude Opus 5.5, around them. All three are small, so read counts, not decimals.

## Development set

30 RFQs. Policy requires a person on 15 of them, so at most 50.0% can be auto-approved.

| Metric | `baseline` | `llm-v1` | `llm-v2` |
|---|---:|---:|---:|
| Header field accuracy | 63.3% | 95.0% | 99.2% |
| Line item extraction F1 (quantity, unit) | 76.7% | 100.0% | 100.0% |
| End-to-end line F1 (SKU, quantity, unit) | 67.1% | 100.0% | 100.0% |
| RFQs with every line exactly right | 43.3% | 100.0% | 100.0% |
| Must-review quotes that reached a person | 86.7% | 100.0% | 100.0% |
| Clean quotes held anyway | 66.7% | 86.7% | 6.7% |
| Auto-approved share | 23.3% | 6.7% | 46.7% |
| Unsafe auto-approvals (count) | 4 | 0 | 0 |
| Requests that failed and went to a person (count) | 0 | 0 | 0 |
| LLM cost per RFQ (USD) | $0.0000 | $0.0010 | $0.0011 |
| LLM time per RFQ, summed over calls (s) | 0.0 | 7.0 | 6.7 |
| LLM calls per RFQ | 0.0 | 3.7 | 3.7 |

Header accuracy by field:

| Field | `baseline` | `llm-v1` | `llm-v2` |
|---|---:|---:|---:|
| customer_name | 96.7% | 100.0% | 100.0% |
| customer_email | 96.7% | 96.7% | 100.0% |
| delivery_date | 56.7% | 86.7% | 100.0% |
| delivery_location | 3.3% | 96.7% | 96.7% |

## Held-out set

10 RFQs. Policy requires a person on 4 of them, so at most 60.0% can be auto-approved.

| Metric | `baseline` | `llm-v1` | `llm-v2` |
|---|---:|---:|---:|
| Header field accuracy | 62.5% | 97.5% | 100.0% |
| Line item extraction F1 (quantity, unit) | 60.0% | 100.0% | 100.0% |
| End-to-end line F1 (SKU, quantity, unit) | 33.3% | 100.0% | 100.0% |
| RFQs with every line exactly right | 10.0% | 100.0% | 100.0% |
| Must-review quotes that reached a person | 50.0% | 100.0% | 100.0% |
| Clean quotes held anyway | 100.0% | 66.7% | 16.7% |
| Auto-approved share | 20.0% | 20.0% | 50.0% |
| Unsafe auto-approvals (count) | 2 | 0 | 0 |
| Requests that failed and went to a person (count) | 0 | 0 | 0 |
| LLM cost per RFQ (USD) | $0.0000 | $0.0009 | $0.0010 |
| LLM time per RFQ, summed over calls (s) | 0.0 | 6.0 | 5.6 |
| LLM calls per RFQ | 0.0 | 2.9 | 2.9 |

Header accuracy by field:

| Field | `baseline` | `llm-v1` | `llm-v2` |
|---|---:|---:|---:|
| customer_name | 90.0% | 100.0% | 100.0% |
| customer_email | 100.0% | 100.0% | 100.0% |
| delivery_date | 60.0% | 90.0% | 100.0% |
| delivery_location | 0.0% | 100.0% | 100.0% |

## Harder set

32 RFQs. Policy requires a person on 13 of them, so at most 59.4% can be auto-approved.

| Metric | `baseline` | `llm-v1` | `llm-v2` |
|---|---:|---:|---:|
| Header field accuracy | 75.0% | 100.0% | 100.0% |
| Line item extraction F1 (quantity, unit) | 30.4% | 99.0% | 100.0% |
| End-to-end line F1 (SKU, quantity, unit) | 23.2% | 97.9% | 100.0% |
| RFQs with every line exactly right | 0.0% | 93.8% | 100.0% |
| Must-review quotes that reached a person | 84.6% | 100.0% | 100.0% |
| Clean quotes held anyway | 94.7% | 63.2% | 15.8% |
| Auto-approved share | 9.4% | 21.9% | 50.0% |
| Unsafe auto-approvals (count) | 3 | 0 | 0 |
| Requests that failed and went to a person (count) | 0 | 0 | 0 |
| LLM cost per RFQ (USD) | $0.0000 | $0.0012 | $0.0012 |
| LLM time per RFQ, summed over calls (s) | 0.0 | 7.5 | 7.2 |
| LLM calls per RFQ | 0.0 | 4.0 | 4.0 |

Header accuracy by field:

| Field | `baseline` | `llm-v1` | `llm-v2` |
|---|---:|---:|---:|
| customer_name | 100.0% | 100.0% | 100.0% |
| customer_email | 100.0% | 100.0% | 100.0% |
| delivery_date | 100.0% | 100.0% | 100.0% |
| delivery_location | 0.0% | 100.0% | 100.0% |

## Matcher in isolation

Each matcher is given the gold line text, so extraction errors are excluded. The gold text is a clean, self-contained description, which is easier than raw wording.

| Matcher | Sets | SKU accuracy | Wrong SKU with high confidence | Cost (USD) |
|---|---|---:|---:|---:|
| `top-hit` | Development and held-out sets | 92.1% of 101 | 3 | $0.0000 |
| `top-hit` | Harder set | 79.2% of 96 | 11 | $0.0000 |
| `llm-match` | Development and held-out sets | 100.0% of 101 | 0 | $0.0247 |
| `llm-match` | Harder set | 100.0% of 96 | 0 | $0.0228 |

## Case by case: `llm-v2`

| RFQ | Set | What makes it hard | Lines right | Expected | Route | Review reasons |
|---|---|---|---|---|---|---|
| rfq-001 | dev | clean English list | 3/3 | auto_approve | auto_approve | - |
| rfq-002 | dev | clean German list, decimal comma in sizes | 2/2 | auto_approve | auto_approve | - |
| rfq-003 | dev | sloppy lowercase German, '2x', relative date | 1/1 | human_review | human_review | MARGIN_BELOW_FLOOR |
| rfq-004 | dev | kilometres, calendar week, one product we do not sell | 2/2 | human_review | human_review | UNMATCHED_ITEM |
| rfq-005 | dev | drums times length must become metres | 1/1 | auto_approve | auto_approve | - |
| rfq-006 | dev | no quantities given | 2/2 | human_review | human_review | MISSING_QUANTITY, OPEN_QUESTION |
| rfq-007 | dev | table in an email, units implied | 6/6 | auto_approve | auto_approve | - |
| rfq-008 | dev | forwarded internally; customer is in the quoted mail | 2/2 | auto_approve | auto_approve | - |
| rfq-009 | dev | cable type not stated: must not be guessed | 1/1 | human_review | human_review | OPEN_QUESTION, UNMATCHED_ITEM |
| rfq-010 | dev | customer corrects a quantity in the same mail | 2/2 | auto_approve | auto_approve | - |
| rfq-011 | dev | drums without a length: unit cannot be converted | 1/1 | human_review | human_review | BELOW_MOQ, OPEN_QUESTION, UNIT_MISMATCH |
| rfq-012 | dev | calendar week; delivery earlier than the lead time (the only reason to hold) | 1/1 | human_review | human_review | LEAD_TIME_RISK |
| rfq-013 | dev | quantities as German number words, date without year | 2/2 | auto_approve | auto_approve | - |
| rfq-014 | dev | below the minimum order quantity | 1/1 | human_review | human_review | BELOW_MOQ |
| rfq-015 | dev | request mixed with an unrelated invoice question | 1/1 | human_review | human_review | OPEN_QUESTION |
| rfq-016 | dev | items in a German sentence; joint chosen by cable size | 3/3 | auto_approve | auto_approve | - |
| rfq-017 | dev | English thousands separator; project size as distractor | 2/2 | auto_approve | auto_approve | - |
| rfq-018 | dev | German thousands separator (1.500 m is 1500 m); total over the sign-off limit | 3/3 | human_review | human_review | HIGH_ORDER_VALUE |
| rfq-019 | dev | not an RFQ at all (delivery complaint) | 0/0 | human_review | human_review | NO_LINE_ITEMS, OPEN_QUESTION |
| rfq-020 | dev | one product outside our range | 2/2 | human_review | human_review | UNMATCHED_ITEM |
| rfq-021 | dev | internal phone note, no customer email | 2/2 | human_review | human_review | LOW_MATCH_CONFIDENCE, MISSING_CUSTOMER_EMAIL, OPEN_QUESTION |
| rfq-022 | dev | chat paste, two items on one line | 2/2 | auto_approve | auto_approve | - |
| rfq-023 | dev | clean German bill of quantities | 8/8 | auto_approve | auto_approve | - |
| rfq-024 | dev | quantity and unit share one cell | 5/5 | auto_approve | auto_approve | - |
| rfq-025 | dev | subtotal rows that look like line items | 6/6 | auto_approve | human_review | OPEN_QUESTION |
| rfq-026 | dev | specification spread over several columns; high value | 4/4 | human_review | human_review | HIGH_ORDER_VALUE, MARGIN_BELOW_FLOOR |
| rfq-027 | dev | formal letter, items in prose, a quantity in words | 3/3 | auto_approve | auto_approve | - |
| rfq-028 | dev | tender table; near-identical 10 kV and 20 kV lines; large order | 6/6 | human_review | human_review | HIGH_ORDER_VALUE, LEAD_TIME_RISK, MARGIN_BELOW_FLOOR |
| rfq-029 | dev | descriptions wrap over two lines, quantity at the end | 5/5 | human_review | human_review | BELOW_MOQ |
| rfq-030 | dev | km to metres, 5x2.5 next to 5x25, relative date | 3/3 | auto_approve | auto_approve | - |
| rfq-031 | holdout | prompt injection inside the request | 2/2 | human_review | human_review | OPEN_QUESTION |
| rfq-032 | holdout | answer to our own question, on top of a quoted thread | 1/1 | auto_approve | auto_approve | - |
| rfq-033 | holdout | rolls and boxes with a stated size each | 2/2 | auto_approve | auto_approve | - |
| rfq-034 | holdout | price question with no quantity | 1/1 | human_review | human_review | MISSING_QUANTITY, OPEN_QUESTION |
| rfq-035 | holdout | second sheet holds the items; 'lfm' and text quantities | 3/3 | auto_approve | auto_approve | - |
| rfq-036 | holdout | a cancelled position must be left out; week in next year | 2/2 | auto_approve | human_review | OPEN_QUESTION |
| rfq-037 | holdout | a size we do not carry, next to close neighbours | 2/2 | human_review | human_review | OPEN_QUESTION, UNMATCHED_ITEM |
| rfq-038 | holdout | voicemail transcript: everything in words | 2/2 | auto_approve | auto_approve | - |
| rfq-039 | holdout | target price in the request must not become a quantity | 1/1 | human_review | human_review | OPEN_QUESTION |
| rfq-040 | holdout | 'respectively': quantities listed apart from products | 3/3 | auto_approve | auto_approve | - |
| hard-001 | hard | near-miss variant | 2/2 | auto_approve | auto_approve | - |
| hard-002 | hard | product not in the catalog; unit conversion | 5/5 | human_review | human_review | UNMATCHED_ITEM |
| hard-003 | hard | unit conversion | 1/1 | auto_approve | auto_approve | - |
| hard-004 | hard | German and English number formats mixed; typos and trade abbreviations | 5/5 | auto_approve | auto_approve | - |
| hard-005 | hard | forwarded chain with a corrected quantity | 3/3 | auto_approve | human_review | OPEN_QUESTION |
| hard-006 | hard | pasted, misaligned table; German and English number formats mixed | 3/3 | auto_approve | auto_approve | - |
| hard-007 | hard | item mentioned only as context | 4/4 | auto_approve | auto_approve | - |
| hard-008 | hard | typos and trade abbreviations; ambiguous on purpose | 3/3 | human_review | human_review | UNMATCHED_ITEM |
| hard-009 | hard | ambiguous on purpose | 1/1 | human_review | human_review | UNMATCHED_ITEM |
| hard-010 | hard | near-miss variant; forwarded chain with a corrected quantity | 2/2 | auto_approve | auto_approve | - |
| hard-011 | hard | product not in the catalog | 3/3 | human_review | human_review | UNMATCHED_ITEM |
| hard-013 | hard | German and English number formats mixed | 4/4 | auto_approve | human_review | OPEN_QUESTION |
| hard-015 | hard | pasted, misaligned table | 5/5 | human_review | human_review | MARGIN_BELOW_FLOOR |
| hard-016 | hard | item mentioned only as context; product not in the catalog | 1/1 | human_review | human_review | UNMATCHED_ITEM |
| hard-017 | hard | typos and trade abbreviations | 5/5 | auto_approve | auto_approve | - |
| hard-018 | hard | ambiguous on purpose; item mentioned only as context | 1/1 | human_review | human_review | UNMATCHED_ITEM |
| hard-019 | hard | near-miss variant | 5/5 | auto_approve | auto_approve | - |
| hard-020 | hard | product not in the catalog; unit conversion | 5/5 | human_review | human_review | UNMATCHED_ITEM |
| hard-021 | hard | unit conversion | 2/2 | auto_approve | auto_approve | - |
| hard-022 | hard | German and English number formats mixed; typos and trade abbreviations | 4/4 | auto_approve | auto_approve | - |
| hard-023 | hard | forwarded chain with a corrected quantity | 1/1 | auto_approve | auto_approve | - |
| hard-024 | hard | pasted, misaligned table; German and English number formats mixed | 4/4 | auto_approve | auto_approve | - |
| hard-025 | hard | item mentioned only as context | 3/3 | auto_approve | auto_approve | - |
| hard-026 | hard | typos and trade abbreviations; ambiguous on purpose | 2/2 | human_review | human_review | OPEN_QUESTION, UNMATCHED_ITEM |
| hard-027 | hard | ambiguous on purpose | 2/2 | human_review | human_review | UNMATCHED_ITEM |
| hard-029 | hard | product not in the catalog | 2/2 | human_review | human_review | UNMATCHED_ITEM |
| hard-030 | hard | unit conversion; near-miss variant | 3/3 | auto_approve | auto_approve | - |
| hard-031 | hard | German and English number formats mixed | 3/3 | auto_approve | human_review | OPEN_QUESTION |
| hard-033 | hard | pasted, misaligned table | 3/3 | auto_approve | auto_approve | - |
| hard-034 | hard | item mentioned only as context; product not in the catalog | 4/4 | human_review | human_review | UNMATCHED_ITEM |
| hard-035 | hard | typos and trade abbreviations | 1/1 | auto_approve | auto_approve | - |
| hard-036 | hard | ambiguous on purpose; item mentioned only as context | 4/4 | human_review | human_review | UNMATCHED_ITEM |

⚠️ marks an unsafe auto-approval: approved although a line, the recipient or the delivery date is wrong, or although policy requires a person.

