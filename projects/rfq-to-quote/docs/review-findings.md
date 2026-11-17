# Review findings: second iteration

Before the first paid model run, the project was put through an independent
review with one brief: assume the code is wrong, and say what a senior forward
deployed engineer would object to. The review ran the baseline, a fake model that
returns perfect answers, and the SDK's parsing code offline. No API calls were
made.

This page lists the nine problems that review found in the code, what each one
would have caused, and how it was fixed. The other changes that came out of the
same review are summarised below the table.

## The nine problems

| # | Problem | What it would have caused | Fix | Covered by |
|---|---|---|---|---|
| 1 | Three gaps in the guardrails: a quantity with no unit, a missing customer name, and the extractor's own "open questions" never held a quote | A request for "150" of a cable, with the model noting "150 drums or 150 m?", was auto-approved at EUR 6,378 | New rules `UNKNOWN_UNIT`, `MISSING_CUSTOMER_NAME` and `OPEN_QUESTION`; anything the extractor says a rep must resolve now holds the quote | `test_quote_guardrails.py` |
| 2 | A malformed model reply crashed the pipeline. The SDK validates the reply while parsing, and a reply cut off at the token limit raises a validation error the code did not catch | One bad reply would have stopped a batch instead of sending that request to a person. The tests faked the SDK, so they could not see it | The validation error is caught and turned into `PIPELINE_ERROR`, which routes to review | `test_llm.py` |
| 3 | Re-running the agent overwrote reviewer decisions. Results were stored with "insert or replace" | A rejected quote went back to pending and the reviewer's note was lost; deciding on an unknown id did nothing, silently | A decided quote is never replaced, re-processing is logged, and an unknown id raises an error | `test_evals_and_store.py` |
| 4 | A reviewer correction replaced the hand-written label with fields nobody had reviewed, and the review screen could not edit the header | Correcting one quantity on a sample request silently changed its gold delivery location, and with it the eval numbers | Header fields are editable and shown to the reviewer; a hand label always wins over a correction; corrections made while trying the demo are git-ignored | `test_evals_and_store.py`, `test_review_ui.py` |
| 5 | "Unsafe auto-approval" only looked at line items | A quote addressed to the colleague who forwarded the email was auto-approved and counted as safe | The metric now also fails on a wrong recipient or delivery date, and a new rule `INTERNAL_RECIPIENT` holds any quote addressed to our own domain | `test_evals_and_store.py`, `test_pipeline.py` |
| 6 | The lead-time check used the email's date as "today", and `--as-of` could not override it | An old email processed weeks later was auto-approved with a delivery date already in the past | Lead time is checked against the processing date (today by default); `--as-of received` replays old mail on its own date | `test_pipeline.py` |
| 7 | One unreadable file stopped the whole batch, text files in a legacy encoding failed, and email attachments were ignored | A single bad file blocked every request behind it; an RFQ sent as an attached PDF looked empty | An unreadable file becomes a review case; text falls back to a second encoding; attachments are read, and any that cannot be read raise `UNREAD_ATTACHMENT` | `test_intake.py`, `test_pipeline.py` |
| 8 | The eval report's "lines right" column printed correct lines over gold lines only | A request with all 6 real lines plus 2 invented ones showed "6/6" | The column now shows the extras, for example "6/6 (+2 wrong)" | Report output |
| 9 | One failed matching call threw away the whole extraction | A rate limit on the last of ten lines discarded nine good lines | Only the failed line is left unmatched, with the reason; the rest of the quote is kept | `test_pipeline.py` |

## Changes to the evaluation

- **A held-out set.** The prompts and the catalog synonyms had been written with
  all 30 cases in view. Ten new cases were written after the prompts were frozen
  and are reported separately. They are by the same author, so they reduce the
  problem without removing it.
- **Prompt examples no longer quote the test cases.** The rule prompt used
  wording lifted from the labelled requests. Its examples were rewritten.
- **A fairer comparison of the two prompts.** The short prompt now also receives
  the received date, and the schema descriptions no longer carry reading rules,
  so the difference between the prompts is the rules and nothing else.
- **Routing has a second number.** "Clean quotes held anyway" was added, and the
  report states the ceiling: the share of requests policy allows to be
  auto-approved at all.
- **Every money and date rule has a case where it is the only reason to hold.**
  Before, lead time and order value only ever fired together with the margin
  rule, so a misread date could never show up as an unsafe approval.
- **Failures are counted.** Requests that failed and went to a person are a row
  in the report instead of looking like model mistakes.

## Changes before spending on the model

- **Recordings cannot go stale.** The replay key covered only the schema's name.
  It now covers the full schema and the request settings, and the file is written
  atomically.
- **The command line and the eval send the same prompt.** Attachments get their
  received date from an envelope file, where before only the eval supplied it, so
  a recorded eval run could not be replayed from the command line.
- **Effort is set explicitly** and is part of the replay key, so cost does not
  depend on a model default.
- **Cost is attributed to the model that answered**, which matters when a
  fallback model serves a request.
- **Descriptions are self-contained.** The extractor is told that each line is
  matched on its own, so "20 pcs 400 mm" after a cable ladder carries the product
  name with it.
- **The confidence labels no longer overlap.** "High" and "medium" could both
  apply to a product with an unstated attribute; the definitions were separated.

## Changes to the labelled data

- A request that also contained an invoice complaint was labelled auto-approvable.
  It now expects a person.
- One sample asked for copper lugs on an aluminium cable, which a sales rep would
  query. The cable was changed to copper.
- Two samples were changed so that lead time and order value each have a case of
  their own (see above).

## Production concerns that were added

- **Prompt injection.** The extractor is told the document is data. Independently
  of the model, the recipient must appear in the source and must match the
  external sender, and one held-out case contains an injection attempt.
- **Audit trail.** Every result records the document hash, model, prompt hash,
  catalog hash and policy; every action is appended to an event log with the
  actor.
- **Assisted mode.** A switch that auto-approves nothing, for the first stage of
  a rollout.
- **Monitoring.** `rfq2quote stats` reports volume, hold reasons, override rate
  and model cost from the review queue.
- **A line limit.** Matching costs one model call per line, so lines beyond 80
  are left for a person.

## After the review: the first model runs

- The Claude path worked on the first live call, including the refusal fallback
  together with structured output.
- The evaluation was first run one call at a time and took about 13 minutes.
  Matching and scoring were made concurrent, with a cap on calls in flight, and a
  progress line is printed as requests finish.
- The default model was the largest one, chosen without evidence. Measured on the
  labelled sets, the smallest current model had no unsafe auto-approval, and it
  became the default.
- The first run exposed a mistake in the rule prompt: it converted "twelve rails
  of 4.4 m" to 52.8 m. The unit guardrail held both affected quotes. The prompt
  was corrected using development cases only.

## Still open

- The published tables come from a single run of one model on 40 cases, and the
  scores are at the ceiling, so those sets can no longer separate configurations.
  Two things were added later (README, section 9): repeat runs, which measure the
  run-to-run variation, and a harder set of 32 requests, which separates rules
  from models and one prompt from the other but not the three models. Cases
  written by a second person are still missing.
- The model raises open questions a rep would not, which holds some clean quotes.
- Money is held as rounded floats; a production system would use decimals.
- The review screen has no login. The reviewer's name is typed in.
