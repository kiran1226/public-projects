# Decision log

Choices made while building the agent, with the alternatives and the trade-off
accepted. Where a decision still needs evidence from a recorded model run, that is
stated.

## 1. An agent loop here, a fixed pipeline elsewhere

**Decision.** The model chooses which tool to call next, inside hard limits on
steps, delegations and cost.

**Alternative.** A fixed pipeline, as in [rfq-to-quote](../../rfq-to-quote/).

**Why.** A quote always has the same five steps. An investigation does not: one
alert is settled by a note in the customer file, another needs the counterparty's
other accounts. Fixing the steps would either waste calls on easy alerts or stop
short on hard ones.

**Trade-off.** Cost and latency vary per case, and a loop has more ways to fail.
Limits, a per-case cost cap and "no failure produces a close" carry that risk.

## 2. Two tiers plus workers, not one model

**Decision.** Haiku triages unflagged alerts, Opus leads the rest, Haiku workers do
bulk reading for the lead.

**Alternatives.** One large model for everything; one small model for everything.

**Why.** Most alerts are routine and an Opus token costs 40 times a Haiku token at
list price.

**Measured.** One recorded run each on the 40 alerts; none closed a suspicious
alert.

| Configuration | Right outcome | Typology right | Cost per alert |
|---|---|---|---|
| Haiku in every role | 38 of 40 | 8 of 9 | $0.003 |
| Routed, Opus lead at medium effort | 40 of 40 | 8 of 9 | $0.032 |
| Opus alone at low effort, no triage | 40 of 40 | 9 of 9 | $0.030 |

**Outcome.** The decision did not hold up on this set. One large model on every
alert matched the routed design, was simpler and cost slightly less: the routed
lead runs at a higher effort, and triage closed only half of the legitimate
alerts, so the cheap tier saved less than the expensive tier spent. Haiku alone is
a tenth of the cost and missed two legitimate hard cases by asking for more
information, a safe error. The workers were never called, because no account in
the dataset is large enough to need them.

**What I would do next.** Take Haiku alone or Opus alone into shadow mode, and
measure a routed setup with the lead at low effort, which was not run. The routing
code stays: it is configuration, and real alert volumes may split differently from
a 40-alert synthetic set.

## 3. Code computes, models interpret

**Decision.** Structuring counts, pass-through ratios, turnover against profile and
name similarity are computed in Python and given to the model as facts.

**Why.** Arithmetic must be exact and repeatable, and the compliance team must be
able to read the rule that produced a number.

## 4. Pseudonymise at a gateway

**Decision.** Every tool result passes through one module that replaces identifiers
with tokens before a model sees it.

**Alternatives.** Send raw data under a contract; ask the model to ignore
identifiers; redact with a model.

**Why.** The data protection officer's condition was that no real identifier
reaches an external model. A contract does not reduce exposure, an instruction is
not a control, and redacting with a model sends the data to a model.

**Trade-off.** The model cannot use what a name would tell a human. Screening
therefore runs in code (decision 8).

## 5. Tokens are per case

**Decision.** `CUST_01` means a different customer in every case.

**Alternative.** Stable tokens across cases.

**Why.** Traces and recordings from two cases cannot be joined to build a profile
of one customer.

**Trade-off.** The agent cannot recognise the same counterparty across cases. Links
between parties come from the bank's own link table instead.

## 6. The vault is the evidence ledger

**Decision.** A finding may cite only tokens the gateway issued in that case and
showed to the model that cites them; code checks every citation.

**Why.** One mechanism gives both privacy and grounding. A citation to something
that was never retrieved cannot be real, and the check needs no model.

**Tightened after review.** The first version accepted any token issued in the
case, including one that only a worker had seen. The check now uses the tokens
that appeared in the citing model's own brief and tool results. Replaying the
three recordings gives the same report, so no recorded case relied on the gap.

**Trade-off.** The check shows that evidence was retrieved, not that it supports
the statement next to it. Judging support needs a reader, and here that is the
analyst.

## 7. Scrubbing fails closed

**Decision.** In free text, known parties become tokens; any other capitalised word
that is not ordinary vocabulary is redacted.

**Alternative.** Redact only what a dictionary of known identifiers finds.

**Why.** A dictionary misses a name it has never seen, such as a relative mentioned
in a payment reference. Failing closed costs some over-redaction and removes that
gap.

**Trade-off.** The vocabulary is a list that has to be maintained, and an unknown
name in lower case passes. Known persons are caught in any case, and by surname
alone, after an independent review showed those spellings got through. The outbound check is the second line; a production system would
add a name recogniser that runs inside the bank.

**Measured.** 188 of 188 identifier-bearing free-text fields in the dataset are
clean after scrubbing.

## 8. Screening stays inside the boundary

**Decision.** Name screening and media search run in code on real names. The model
receives name similarity, date-of-birth match and country match.

**Why.** Showing a watchlist name next to a similarity score would reveal roughly
what the customer is called.

## 9. Sensitive categories behind a logged request

**Decision.** Counterparties tagged health, religion, political or trade union are
withheld. The lead may request the category with a justification; the request is
logged and flagged to the analyst.

**Why.** These payments reveal things about a person that the investigation rarely
needs. When it does need them, the access should leave a trace.

**Measured.** In the recorded runs the routed configuration and Haiku alone each
made one request in 40 alerts, both on the one alert where the labels mark the
category as relevant (the clinic patient). The routed lead then closed that alert;
Haiku still asked for more information. Opus alone made no request and closed the
same alert without the category, at medium confidence. So the control was used
when it was needed in two configurations and skipped in the third, and nothing
asked without need.

## 10. A hand-written loop, not the SDK tool runner

**Decision.** The loop is about a hundred lines over a one-method provider
interface.

**Why.** Every model step has to be keyed, recorded, replayed without a key,
budget-checked before it is sent and leak-scanned. That is a custom transport, and
the same loop then runs unchanged on another vendor.

## 11. Tool calls run in order

**Decision.** Several tool calls in one model turn run one after another.

**Alternative.** Run them in parallel.

**Why.** Tools issue tokens. In parallel, token numbers would depend on thread
timing, the next request would differ between a recorded run and its replay, and
the replay would miss.

**Trade-off.** Latency on a turn with several delegations.

## 12. A strict submit tool, not forced tool choice

**Decision.** The loop ends when the model calls a submit tool with a strict
schema. If it does not, it is re-prompted once.

**Why.** The current large model rejects forced tool choice. A strict tool keeps
the schema guarantee, and the single re-prompt covers the rare turn that ends
without a call.

## 13. Record and replay, with a leak scan over recordings

**Decision.** Responses are stored keyed by the full request, together with what
was sent at each step. A scan reads everything that was sent.

**Why.** Evaluations are reproducible and free to re-run, CI exercises the model
path without a secret, and the privacy claim can be re-checked by anyone who clones
the repository.

## 14. A spend ledger with a per-call reserve

**Decision.** Before each live call the run stops if the remaining budget is below
a reserve that bounds the cost of that call.

**Why.** Checking after the call would allow one overshoot per run. A damaged
ledger stops the run, because reading it as zero would remove the cap.

**Tightened after review.** The first reserve was a fixed amount per model that
assumed a request of at most 30,000 input tokens, and nothing bounded the size of
a request. The reserve is now computed from the request itself: its size in bytes
as an upper bound on input tokens, plus the output limit, at the highest price the
call can be billed at. On the recorded requests it stays between $0.30 and $0.34
for Opus, so the run caps did not need to change.

**Not covered.** A declined request that a fallback model then answers: the
declined attempt is not written to the ledger.

## 15. Assisted mode by default

**Decision.** Every disposition is a recommendation until the sponsor decides
otherwise.

**Why.** Closing alerts automatically is a decision for the bank to take on
evidence. The evaluation reports what auto-close would have done.

## 16. Scoring in code

**Decision.** Outcomes, typologies, cited evidence and routing are scored against
gold labels in Python.

**Alternative.** An LLM judge for the narratives.

**Why.** The questions that matter here have checkable answers, a judge adds noise
and cost, and the same labels would have to grade the judge.

**Trade-off.** The quality of the prose in a narrative is not scored. The level-2
investigator reviews it in shadow mode.

## 17. Expected routing is derived

**Decision.** Whether an alert should have reached the lead is computed from the
gold labels and the policy.

**Why.** Hand-labelled routes go stale the day a threshold changes.

## 18. A second vendor behind the same interface

**Decision.** An NVIDIA-hosted model runs through the same loop, tools, gateway and
evaluation. Its provider states a requested schema in the prompt and validates the
reply.

**Why.** The endpoint does not promise schema-valid tool arguments, and structured
output differs by backend. Relying on neither keeps one code path, and the
validation it forces (tool arguments, submissions, worker digests) protects the
Claude path as well.

**Not verified.** The adapter is tested against the documented request and reply
format, never against the live endpoint: no NVIDIA key was used for this
repository. What stands on evidence is the interface and the validation; whether a
hosted model does the job in each role is unknown.

## 19. A scenario corrected after the first development round

**Decision.** The seasonal-business scenario was changed in the generator after
the first live development round: the account now shows cash takings in June, and
the customer note states that summer months run at three to four times the
declared average.

**Why.** The first version contradicted itself. The customer note said takings
arrive from June to August, the generated account had no cash in June, and July
ran at more than three times the declared turnover with nothing on file to explain
it. The label said "close". The model asked for last year's accounts instead,
which was the sound answer to the data it was given. The data was wrong, not the
model.

**How it was done.** The fix is in `scripts/build_dataset.py` and applies to all
six seasonal alerts, including the two held-out ones. It was made before any
held-out run, and no other scenario's values changed (72 added transactions, no
other row differs).

**Trade-off.** A scenario was changed after seeing model output on the development
set. It is recorded here so the reader can weigh it. The same round also tuned the
prompts, on development alerts only: the lead was given a stated standard for when
the bank's records are enough to close, and an evidence token was defined by
example.

## 20. Instructions in the data are stopped in code

**Decision.** The gateway checks every free-text field for text written to the
model. One hit sends the case to manual investigation under the rule
`INSTRUCTION_IN_DATA`, whatever the model proposed.

**Alternatives.** Tell the model in the prompt that free text is untrusted; strip
the suspect text before the model sees it.

**Why.** A review showed that a payment reference saying "ignore all previous
instructions" reached the model unchanged, and that the gate trusted the fields
the model then returned. A prompt instruction is not a control, and it could not
be added here without invalidating the recordings. Stripping the text would hide
from the analyst that someone tried. Routing the case to a person does neither.

**Trade-off.** The check is a list of patterns, so the same request in other
words passes. It is one layer; the others are the hard flags computed in code and
assisted mode. The limit is stated in the README, with what should be done before
auto-close is switched on.

**Measured.** Nothing in the dataset triggers the rule, and replaying the three
recordings gives the same report.
