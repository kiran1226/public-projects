# Decision log

Choices made while building the agent, with the alternatives and the trade-off
accepted. Where a decision still needs evidence from an evaluation run with the
LLM, that is stated.

## 1. A fixed pipeline, not an agent loop

**Decision.** Five fixed steps. The model is called in two of them and never
chooses what happens next.

**Alternatives.** A tool-using agent that decides when to search, price and ask;
an orchestration framework.

**Why.** The workflow has the same shape every time, so there is nothing for an
agent to plan. Fixed steps can be evaluated one by one, cost a known number of
calls, and can be explained to the sales desk in a diagram. A loop would add
latency, cost and failure modes for no gain here.

**Trade-off.** A request that needs a clarifying question is routed to a person
instead of being handled by the agent. That is the intended behaviour for a first
version.

## 2. The LLM reads; code decides money

**Decision.** The model extracts and matches. Prices, discounts, margins and
approval rules are plain Python over catalog data.

**Why.** The pricing lead has to be able to read and change the rules, and
arithmetic must be exact and repeatable. A model that never sees a price cannot
invent one.

## 3. Lexical search (SQLite FTS5) for retrieval

**Decision.** BM25 over normalised tokens, with sizes such as "5x2,5" collapsed
into one token and a crude plural stemmer shared by index and query.

**Alternatives.** Embedding search; sending the whole catalog to the model.

**Why.** In this domain the signal is in codes and numbers, which lexical search
handles well and embeddings handle poorly ("5x2.5" and "5x25" are close in
embedding space and far apart on a construction site). FTS5 ships with Python, so
there is no extra service. On the gold line texts the top search hit alone is
right for 92.1% of 101 lines, and the model only has to choose among eight
candidates.

**Trade-off.** A customer name for a product that shares no token with the catalog
is not retrieved. Synonyms are catalog data, and unmatched lines go to review.

## 4. Declining is a first-class answer

**Decision.** The matcher may return no SKU, and the prompt says so. A model
answer naming a SKU that was not among the candidates is discarded.

**Why.** The desk's condition for trusting the system was that it does not guess.
A request for "5x2.5 cable" with no type fits three products; the right outcome is
a person asking the customer.

## 5. Confidence as three labels, not a number

**Decision.** `high`, `medium`, `low`, each defined in the prompt by what the
model had to assume. Only `high` may be auto-approved.

**Why.** A self-reported probability looks precise and is not calibrated. A label
tied to a stated condition ("had to assume an unstated attribute") is something
the eval can check and a reviewer can understand.

**Result.** In the recorded run the matcher had no wrong SKU at high confidence
on 101 labelled lines. The label text is clean, so this is weak evidence.

## 6. Guardrails are named rules

**Decision.** Each reason for holding a quote is a rule with a code and a
sentence. Thresholds live in one `Policy` object.

**Why.** Reviewers need the reason, and the business needs to change a threshold
without touching a prompt. A failed model call is a rule too: it routes to a
person.

## 7. Expected routing is derived, not labelled

**Decision.** The eval computes the expected route by running the guardrails on
the gold labels.

**Why.** Hand-labelled routes would go stale the day a threshold changes. This way
the labels describe the documents, the policy describes the business, and the eval
checks that the pipeline reaches the same decision as a perfect reader would.

## 8. A rule-based baseline, kept in the repo

**Decision.** A regular-expression extractor and a top-hit matcher run through the
same pipeline and the same eval.

**Why.** It answers "do we need an LLM here?" with a number instead of an opinion,
it shows where rules are sufficient (clean tables), and it lets anyone run the
whole project without an API key. Its unsafe auto-approvals, such as three drums
of 500 m quoted as 500 m, are the clearest argument for the model.

## 9. Record and replay model responses

**Decision.** Responses are stored in a cassette file keyed by model, prompt and
input, and replayed by default. Live calls need an explicit flag.

**Why.** Evaluations become reproducible and free to re-run, CI can exercise the
LLM path without a secret, and nobody spends money by accident.

**Trade-off.** A recording goes stale when anything that shapes the answer changes.
The key covers the model, the request settings, both prompts and the full schema,
so a stale entry is a miss, never a wrong answer.

## 10. Model and provider

**Decision.** Claude through the official SDK with structured outputs, default
`claude-haiku-5-5`, behind a one-method interface. The server-side refusal fallback
is enabled for models that support it.

The first default was the largest model, chosen without evidence. Measured on the
labelled sets, the smallest current model had no unsafe auto-approval and held
every quote policy requires, at about a tenth of a cent per request, so it became
the default. The sets are too small to show that a larger model is not safer on
rare errors; the guardrails and assisted mode carry that risk, not model size.

**Why.** Structured outputs remove a class of parsing failures. The interface
keeps the vendor in one file, so another backend is one class.

## 11. Two prompt versions, kept side by side

**Decision.** `extract_v1` is the three-sentence prompt most projects start with;
`extract_v2` states the rules the dataset's hard cases need (unit conversion,
number formats, corrections, calendar weeks, what not to treat as an item).

**Why.** It makes prompt work measurable: the report shows both in one table.

**Result.** Both prompts read line items equally well (every line right on both
sets). The rule prompt's gain is in dates and in unnecessary holds: the short
prompt held 17 clean quotes by raising open questions, the rule prompt 2. The
rule prompt also caused one error class of its own, since corrected (see the
review findings). On the harder set added later (item 17) the two prompts no longer
read lines equally well: the short prompt got 94 of 96 lines right and held 12 of
19 clean quotes, the rule prompt 96 of 96 and 3.

## 12. The recipient is checked by rules, not trusted from the model

**Decision.** Three rules check the address a quote would go to: it must appear in
the source document, it must not be one of our own, and if the mail came from an
external sender it must be at that sender's domain.

**Why.** Inbound mail is untrusted text that reaches the model. The model cannot
set prices or approvals, so the recipient is the one field through which a planted
instruction could send our prices to someone else. A rule holds that line whether
or not the model was fooled.

**Trade-off.** A customer who asks for the quote to go to a colleague at another
domain is held for review. That is one click for a reviewer.

## 13. Any open question holds the quote

**Decision.** If the extractor reports a point a rep must resolve, the quote is
held, whatever the point is.

**Alternative.** Let the model grade how important each point is.

**Why.** A complaint or a target price in the same mail as a request is exactly
what a rep wants to see, and a model deciding what is important enough is another
judgement to evaluate. The cost is measured as "clean quotes held anyway".

## 14. A held-out set, written after the prompts were frozen

**Decision.** Thirty cases were in view while the prompts were written. Ten more
were written afterwards and are reported separately; the prompts are not changed
in response to them.

**Why.** Without it, a good score could just mean the prompt memorised the test.

**Limit.** Same author, ten cases. It reduces the problem; a second labeller and
shadow data are what remove it.

## 15. One step scored on data the author did not write

**Decision.** The matching step is also run on Billiger.de Products, a public
benchmark of real German shop offers with labels from its authors. The step is
unchanged; the offers each query is paired with act as its catalog.

**Alternatives.** Older product-matching sets such as Abt-Buy and Amazon-Google:
widely reproduced, so a current model has probably seen them. Search-relevance
sets: a query there has many acceptable products, which contradicts the matcher's
rule that a confident match means exactly one candidate fits. No open corpus of
real quotation requests was found, so extraction cannot be tested this way.

**Why.** The requests, the labels and the prompts here share an author, and the
scores sit at the ceiling. Outside data is the one check that does not share
those blind spots. In this benchmark two thirds of the queries have no correct
answer, so it tests declining, which is the behaviour the desk asked for.

**Trade-off.** It covers one step, on consumer goods. The dataset states no
licence, so one pinned file is downloaded and checked, not committed, and the
evaluation needs a network connection once. Recorded responses drop the model's
free-text reason, so no text derived from the offers is stored. The text fields
(titles only) and the prompt were fixed before the first model call, and the
result is reported whatever it is.

**Result.** F1 71.7% against 31.4% for the top search hit, and 592 of 661 correct
declines. 19 of 148 high-confidence picks were wrong by the benchmark's labels,
which the project's own data had not shown. The prompt was left as it is.

**Larger models, added later.** The same queries were run once each with Claude
Sonnet 5.5 and Claude Opus 5.5, prompt unchanged: F1 74.2% and 78.2%. The gain is
recall (242 and 271 of 339 matches found, against 230). The share of
high-confidence picks that are wrong did not fall: 25 of 184 and 23 of 196, against
19 of 148. Query by query, Sonnet's lead over Haiku could be chance (44 to 30,
two-sided exact McNemar test, p = 0.13); Opus's is clearer (60 to 32, p = 0.005).
The default stays Haiku: what goes out unchecked is a high-confidence pick, a
larger model is no better on those, and it costs about 11 or 28 times as much.
One run per model, so no variance estimate.

## 16. Repeat the evaluation, and answer what it finds with a rule

**Decision.** The labelled requests were run live again: five times with the default
model, three times with Claude Sonnet 5.5 and once with Claude Opus 5.5. The
recordings are kept in `data/repeat-runs/` and `rfq2quote repeats` replays them.
A new guardrail, `SIZE_MISMATCH`, holds a quote when the request states a size and
the matched product has a different one.

**Why.** One recorded run had no unsafe approval. Five more showed that this was one
draw: in one of them a size the catalog does not carry (4x16) was matched to its
neighbour (4x25) with high confidence and the quote was auto-approved. The prompt
already tells the matcher to decline when nothing fits, and it did so in three runs
of five. Asking again in firmer words would be a change that cannot be checked. A
comparison of two sizes in code can.

**Alternatives.** Switching the default to Sonnet, whose three runs declined the
size every time: about 17 times the cost per request, and three runs do not prove it
never makes the mistake. Requiring every attribute of the request to appear in the
product: more holds on clean quotes, because customers and the catalog word
attributes differently.

**Trade-off.** The rule covers sizes written as two numbers, such as 4x16 or 5x2.5.
A wrong voltage or rating is not caught by it. It was written after seeing the
failure, so the recordings show it works on that case and adds no hold on a clean
quote in these runs, not how it does on new requests.

## 17. A harder set, with labels fixed in code and texts written by a model

**Decision.** A third evaluation set, `hard`, sits beside the development and
held-out sets: 32 requests, 96 line items. Code drew each case (customer, dates,
and per line the product, quantity, unit and their exact wording) around one or two
of nine difficulties. Claude Opus 5.5 wrote each email around those wordings. The
expected route is derived by the guardrails, as in item 7. `rfq2quote eval` reports
the set in a table of its own, and `rfq2quote hard` replays every recorded run on
it: the rules, both prompts with Haiku, Sonnet, Opus, and five further Haiku runs.

**Why.** The original 40 requests are at the ceiling, so they cannot say whether a
prompt or a model is better. The next step named in the README, cases by a second
person, was not available. Labels drawn by code before any text exists cannot be
bent toward what the system reads well, and they can be checked mechanically: every
labelled wording stands word for word in its request.

**Alternatives.** Writing harder cases by hand: the same author again, and no
check that label and text agree. Letting the model write text and label together:
the label would then be the model's own reading.

**What was left out.** 36 requests were written. Four were not adopted because the
text has a defect that makes the label "clean" doubtful: in two the quoted message
is dated after the mail that quotes it, one says more items will follow, and one
orders cable lugs of a size that fits none of its cables. A text cannot be repaired
without recording new model responses, so the four were dropped, and the recordings
were cut down to the responses the 32 adopted requests use. The first version of
all the texts had been withdrawn earlier: the writing model had added customer
questions the labels did not contain, so the texts were written again from the same
labels with a rule against questions.

**Recordings.** The published recording of the 40 requests is not touched. Runs on
the harder set are in `data/cassettes/hard/` and `data/repeat-runs/hard/`, which
are read and never written.

**Result.** The set separates rules from models (the baseline got 0 of 32 requests
right and approved 3 quotes it should not have; no model run approved one) and the
two prompts from each other (the short prompt got 94 of 96 lines and held 12 of 19
clean quotes; the rule prompt got 95 or 96 and held 0 to 4 in six runs). It does
not separate the three models: all got 96 of 96 lines, and their difference in
clean quotes held is inside Haiku's own spread between runs. The size rule of
item 16 fired in no run, and the set has no missing cable size to test it on.

**Limit.** The texts are model-written and tidy: each product and quantity is one
unbroken phrase, which real mail is not. The author of the system chose the
difficulties, and the rule prompt already names three of them. The choice to
rewrite the texts and the choice of which four to drop were made after seeing
results. The script that drew the cases and called the model is not in this
repository; the files under `data/` are the set.
