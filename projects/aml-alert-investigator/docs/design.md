# Design: AML alert investigation agent

**Status:** implemented as designed on 2026-11-15. Measured numbers are in
`docs/eval-report.md`; the figures in section 9 of this document are the estimates the
design started from. Decisions made while building are in `docs/decision-log.md`.

## 1. Purpose

A tool-using agent for the level-1 alert desk of a bank's financial-crime team. It
takes an alert raised by the transaction-monitoring system, gathers the evidence an
analyst would gather, and proposes a disposition with cited evidence. A person
stays accountable for every outcome.

The project is a portfolio piece for forward deployed engineering. It is meant to
show four things:

1. An agent loop used where the work really is open-ended, in contrast to the fixed
   pipeline in [rfq-to-quote](../../rfq-to-quote/).
2. Model routing that is defended with measurements: a small model for routine work
   and a large model only where the case needs it. A second model vendor sits behind
   the same interface and is scored by the same evaluation.
3. Handling of personal and sensitive data as part of the architecture, not as an
   afterthought.
4. Guardrails, evaluation and human review suited to a regulated workflow.

## 2. The business problem

> The customer is fictional ("Harbourline Bank") and the figures below are
> assumptions for the exercise.

The monitoring system raises alerts on rules such as cash deposits under a
threshold or money leaving an account shortly after it arrives. Most alerts turn
out to be legitimate activity. For each one a level-1 analyst opens four systems
(customer file, transaction history, name screening, case history), forms a view
and writes it down. This takes 20 to 40 minutes per alert, most of it gathering.

The agent does the gathering and the first write-up. It does not file reports,
block accounts or contact customers.

## 3. Scope

**In scope**

- Investigation of one alert at a time, end to end, with a full audit trail.
- Two model tiers plus workers, with code deciding policy.
- A privacy gateway between the bank's data and the models.
- A synthetic dataset with gold labels and a one-command evaluation.
- A mode that runs without an API key (rules baseline and recorded replays).
- A small case viewer for the analyst.

**Out of scope**

- The monitoring rules that raise alerts; alerts are inputs.
- Filing suspicious activity reports or any write action in a bank system.
- Real data or real integrations; integration points are described in the README.
- Network analytics across the whole customer base, embeddings, fine-tuning.
- An LLM judge; all scoring is done in code.

## 4. Flow for one alert

```
alert
  |
  v
[code]  risk features and hard flags            (real data, inside the boundary)
  |
  |-- hard flag ------------------------------------------.
  v                                                        |
[Haiku] triage agent, four read tools                      |
  |-- close with reason code                               |
  |      |                                                 |
  |      v                                                 |
  |   [code] triage-close rule -- fails --.                |
  |      | passes                         |                |
  |      v                                v                v
  |   policy gate                  [Opus] lead investigator
  |-- escalate ------------------>   plans, uses tools, decides
                                     |-- delegate --> [Haiku] transaction digest
                                     |-- delegate --> [Haiku] screening check
                                     |-- delegate --> [Haiku] prior-alert summary
                                     v
                                  [code] policy gate
                                     v
                                  analyst queue (tokens restored for the analyst)
```

Every tool result crosses the **privacy gateway** before it enters a prompt, and
every request is scanned again just before it is sent (section 7).

### Steps

1. **Features and hard flags (code).** Deterministic calculations on the alert and
   the customer: cash share, deposits just under the reporting threshold,
   pass-through ratio, turnover against the declared profile. Hard flags are:
   a watchlist candidate for the customer or an alert counterparty, a politically
   exposed person, a high risk rating, an escalated alert in the last twelve months,
   or an alert amount at or above the policy threshold.
2. **Routing (code).** Any hard flag sends the alert straight to the lead. Otherwise
   triage runs.
3. **Triage (Haiku).** A bounded tool loop that ends with `submit_triage`: close with
   a reason code, or escalate with handoff notes (what was checked, what is
   unresolved).
4. **Triage-close rule (code).** A close stands only if confidence is `high`, the
   reason code is on the allow-list and every citation verifies. Otherwise the alert
   is escalated.
5. **Lead investigation (Opus).** Plans the investigation, delegates bulk reading to
   workers (several delegations in one turn), uses the remaining tools itself, and
   ends with `submit_disposition`.
6. **Policy gate (code).** Named rules decide the queue and attach reasons.
7. **Analyst.** Sees the case with real values restored, approves or overrides.
   Decisions are stored.

## 5. Roles and models

| Role | Model | Effort | Tools | Limits |
|---|---|---|---|---|
| Triage | `claude-haiku-5-5` | `low` | profile, activity summary, prior alerts, transaction list, `submit_triage` | 6 model steps |
| Lead | `claude-opus-5-5` | `medium` | all read tools, `request_sensitive_detail`, `delegate`, `submit_disposition` | 8 model steps, 6 delegations |
| Worker | `claude-haiku-5-5` | `low` | none; one structured-output call | one call |

Model and effort per role are configuration, so the evaluation can run other
assignments without code changes: Haiku in every role, or an NVIDIA-hosted model in
every role (section 9).

**Workers.** Code fetches the data the lead asked about, passes it through the
gateway, and one Haiku call returns a typed digest:

- `transaction_digest`: patterns in a window of raw transactions, each with the
  transaction tokens that show it.
- `screening_check`: whether a watchlist candidate is the same party, judged from
  comparison facts.
- `prior_alert_summary`: what earlier alerts on the customer concluded.

The lead reads digests, not raw rows, which keeps the expensive context small.

**Constraints of the current models that shape the loop**

- Opus 5.5 rejects forced tool choice. The loop therefore ends on a `strict`
  submit tool under `tool_choice: auto`; if the model ends its turn without calling
  it, the loop re-prompts once, then fails to a person.
- Thinking cannot be disabled on Opus 5.5, and its default effort is `medium`.
  Effort is set explicitly per role so cost does not depend on a default.
- Assistant content, including thinking blocks, is appended to the conversation
  unchanged.
- The server-side refusal fallback is enabled for Opus. Haiku has none; a refusal
  routes the case to a person.

## 6. Tools

All tools are read-only Python functions over SQLite. Arguments and results use
case-scoped tokens (section 7), never real identifiers.

| Tool | Returns | Triage | Lead |
|---|---|---|---|
| `get_customer_profile` | KYC fields allowed for the role | yes | yes |
| `get_activity_summary` | code-computed aggregates for a window: flows by month, cash share, top counterparties, structuring and pass-through indicators, turnover against profile | yes | yes |
| `get_prior_alerts` | earlier alerts and their outcomes | yes | yes |
| `list_transactions` | raw rows for a window, filtered and capped | yes | yes |
| `get_counterparty` | what the bank knows about a counterparty | no | yes |
| `get_related_parties` | links by shared address, director or account | no | yes |
| `screen_party` | watchlist candidates with comparison facts | no | yes |
| `search_adverse_media` | scrubbed snippets from a fictional media set | no | yes |
| `request_sensitive_detail` | category of a withheld counterparty; needs a justification | no | yes |
| `delegate` | a worker digest | no | yes |
| submit tool | ends the loop | `submit_triage` | `submit_disposition` |

Tool errors are returned to the model as error results so it can adapt. Several
tool calls in one model turn run in call order and their results go back in one
message. They are not run in parallel on purpose: tools issue tokens, and a fixed
order keeps token numbers, and therefore recorded replays, deterministic.

### Disposition schema

```
outcome         close_false_positive | escalate_l2 | needs_info
reason_code     (close)    documented_expected_activity | consistent_with_profile |
                           recurring_known_counterparty | explained_one_off |
                           screening_false_match
typology        (escalate) structuring | pass_through | layering_related_parties |
                           profile_mismatch | high_risk_jurisdiction |
                           sanctions_match | other
confidence      high | medium | low
findings        list of { statement, evidence_ids }
narrative       (escalate) who, what, when, where and why, written with tokens
open_questions  (needs_info) what to ask the relationship manager
```

`submit_triage` takes the same fields for a close (outcome, reason code, confidence,
findings). For an escalation it takes `handoff_notes` instead: what was checked and
what is unresolved.

Confidence labels are defined in the prompt by what the model had to assume, as in
rfq-to-quote, because a self-reported probability is not calibrated.

## 7. Privacy gateway

**Principle.** The models never see a real identifier. They work on pseudonymised
case data. Real values are restored only inside the bank's side, when an analyst
opens the case.

| Control | Behaviour |
|---|---|
| Pseudonymisation | Names, IBANs, account and customer numbers, emails, phones and addresses become tokens such as `PERSON_03` or `ACCT_11`. Transactions and alerts get tokens too (`TXN_0007`). The mapping is per case, so traces of two cases cannot be joined. It lives in a vault table that no tool reads. |
| Minimisation | Each role has a field allow-list. Date of birth becomes an age band; address becomes city and country. Triage sees fewer fields than the lead. |
| Free-text scrubbing | Transaction descriptions and notes are scanned with a dictionary of known parties and patterns for IBANs, emails, phones and card-like numbers, and tokenised. |
| Sensitive categories | Counterparties tagged as health, religious, political or trade-union are withheld by default: neutral token, category and description hidden. |
| Need-to-know access | `request_sensitive_detail` reveals the category (still not the name) to the lead only, requires a written justification, is logged, and flags the case for the analyst. |
| Screening inside the boundary | Name screening and media search run in code on real names. The model receives comparison facts (name similarity, date-of-birth match, country match), not names. |
| Outbound check | A wrapper around the model provider scans every request against the inventory of real identifiers and the patterns above. On a hit the call is blocked, the kind of hit is logged without the value, and the case goes to manual investigation. |
| Re-identification | Tokens are swapped back only in the case viewer and in `trace --reveal`. |

**Evidence ledger.** Because every identifier the model sees is a token the gateway
issued, the vault doubles as the evidence ledger. A finding may cite only tokens
that were shown, in this case, to the model that cites them. A citation to
something that model never received cannot be real, and the policy gate rejects
the disposition.

**Stated limits.** Pseudonymised data is still personal data; this reduces exposure
and does not make the data anonymous. Dictionary and pattern scrubbing can miss an
unknown name in free text, which is why the outbound check exists and why leakage
is measured. These are engineering controls on synthetic data, not a legal
compliance claim. Regional hosting and zero-retention terms with the model provider
are deployment controls a bank would add; they are described in
`docs/data-protection.md` and not built. The agent has no customer-facing channel,
so it cannot tip a customer off.

## 8. Policy gate

Thresholds live in one `Policy` object. Each rule has a code and a sentence shown
to the analyst.

| Rule | Effect |
|---|---|
| `CITATION_UNVERIFIED` | a finding cites a token the gateway did not issue: manual investigation |
| `FINDING_UNCITED` | a finding has no evidence: manual investigation |
| `LOW_CONFIDENCE` | confidence is `low`: manual investigation |
| `CLOSE_WITH_HARD_FLAG` | a close is proposed on an alert with a hard flag: senior sign-off |
| `LIMIT_EXCEEDED` | step, delegation or case cost limit reached: manual investigation |
| `MODEL_FAILURE` | API error, refusal, malformed output after one retry: manual investigation |
| `LEAK_BLOCKED` | outbound check stopped a request: manual investigation |
| `SENSITIVE_ACCESS_USED` | informational flag on the case |
| `TRIAGE_CONFIDENCE`, `TRIAGE_REASON_NOT_ALLOWED` | a first-tier close does not stand: the alert goes to the lead |
| `QA_SAMPLE` | an automatic close sampled into analyst review |
| `INSTRUCTION_IN_DATA` | free text in the bank's data reads like an instruction to the model: manual investigation (added after review) |
| `INTERNAL_ERROR` | the agent itself failed on the case: manual investigation, and the run continues |

**Queues:** `analyst_review` (recommendation attached), `senior_review`,
`manual_investigation` (no recommendation is trusted), and `auto_closed`.

**Autonomy modes.** `assisted` is the default: every disposition is a
recommendation. `auto-close` lets a triage close that passes every rule go through,
with a fixed share sampled into `analyst_review` for quality checks. The evaluation
reports what `auto-close` would have closed.

## 9. Model layer, replay and budget

**Provider interface.** One method, `step(request) -> response`, over plain
serialisable data: model, effort, system, messages, tools, optional output schema,
`max_tokens`. The agent loop is hand-written on top of it instead of using the SDK
tool runner, because record and replay need every model step to be keyed and stored.

- `AnthropicProvider`: the official SDK, structured outputs for workers, strict
  tools for the loop, prompt caching on the system prompt and tool definitions.
- `CassetteProvider`: replays recorded steps keyed by a hash of the full request;
  records on a miss when a live provider is attached. Each entry stores the request
  next to the response, so the leak scan can read what was sent. A whole
  investigation replays
  with no key, because tool results are deterministic and recorded assistant content
  is fed back unchanged. Nothing time-dependent enters a prompt; each alert carries
  its own as-of date.
- `NvidiaProvider`: a second vendor. The NVIDIA API catalog exposes an
  OpenAI-compatible chat endpoint, so this class translates the step format to chat
  completions and back. The endpoint does not guarantee that tool arguments match the
  schema, and structured output differs by backend, so the provider relies on
  neither: a requested schema is stated in the prompt, and the tool layer, the loop
  and the workers validate what comes back.
- `RouterProvider`: sends each request to the vendor that serves its model.
- `ScriptedProvider`: scripted responses for tests.
- `GuardedProvider`: the outbound check from section 7, wrapped around all of them,
  so both vendors are covered by the same check and the same leak scan.

**Budget.** Total API credit for the project is USD 5.

| Item | Estimate |
|---|---|
| Smoke tests while developing, mostly Haiku | 0.70 |
| Haiku in every role, full set | 0.30 |
| Routed configuration, full set, recorded once | 2.20 |
| Reserve for one re-run | 1.80 |

Basis for the estimate, at list prices of USD 0.10 / 0.50 per million tokens for
Haiku 5.5 and 4 / 20 for Opus 5.5: a triage case is about 27,000 cumulative input
tokens and 2,000 output tokens (about USD 0.004); a lead case is about 23,000 input
and 4,000 output tokens (about USD 0.17) when the lead reads digests.

Controls:

- Live calls are opt-in (`--live`); recording is a second flag.
- A spend ledger in `var/` records the cost of every live call, including cache
  reads and writes. Before each call a reserve is held that bounds the cost of
  that call: the request's size in bytes as an upper bound on input tokens, plus
  that role's `max_tokens`; calls in flight hold their reserves together, so the
  cap cannot be overshot even by parallel cases.
- A per-case cost limit feeds `LIMIT_EXCEEDED`.
- Prompts are tuned on Haiku first, so the Opus run happens once.
- The NVIDIA trial tier is rate-limited and not priced per token, so it is outside
  the USD 5 budget. Its guard is a request cap and a minimum interval between calls;
  the ledger still records its token counts. Only synthetic data is sent to it.

## 10. Data

Everything is synthetic and generated by a seeded script, so labels are true by
construction. Names, IBANs and the watchlist are invented; no real person or listed
entity appears.

- About 40 customers (retail and small business) with KYC profiles.
- About 3,500 transactions over six months, with free-text descriptions that
  contain names, IBANs and emails on purpose.
- Counterparties, some tagged with a sensitive category.
- A fictional watchlist with name variants, a small adverse-media set, prior alerts.
- About 40 alerts, split into a development set and a held-out set. Roughly a third
  are hard.

Source files are committed as CSV and JSON; the SQLite database is built from them
on first use.

| Kind | Scenarios |
|---|---|
| Suspicious | structuring under the reporting threshold; rapid pass-through; layering between related parties; activity that does not fit the profile; transfers to high-risk jurisdictions; a true watchlist match |
| Legitimate | documented property sale; seasonal business; annual bonus; recurring supplier; tuition paid abroad; a watchlist candidate with a different date of birth |

Each alert has a gold file: outcome, typology, difficulty (`clear` or
`needs_investigation`), the key evidence a good write-up must cite, and whether a
sensitive detail is relevant.

The set is enriched with hard cases so the evaluation has something to measure.
The README says so, and the impact estimate uses a realistic mix instead.

## 11. Evaluation

One command scores a configuration and writes `docs/eval-report.md`.

| Metric | Question |
|---|---|
| Suspicious alerts closed | The unsafe error. Target zero. Reported per tier. |
| Under-escalation | Gold-suspicious alerts closed at triage. |
| Over-escalation | Clear, unflagged alerts sent to the lead. |
| Disposition accuracy | Outcome against gold; typology on suspicious alerts. |
| Key-evidence recall | Share of gold key evidence cited in findings. |
| Citation validity | Share of cited tokens the gateway issued. |
| Leakage | Real identifiers found in any outbound request. Target zero. |
| Scrubber catch rate | Identifiers in raw text removed by the gateway before the outbound check. |
| Sensitive access | Requests made, and how many were on alerts where gold marks it relevant. |
| Cost, latency, steps | Per alert, by tier and model. |

Expected routing is derived by running the policy on the gold labels, so labels do
not go stale when a threshold changes. `needs_info` on a gold-suspicious alert
counts as safe and is reported separately.

**Configurations**

| Configuration | Cost | Purpose |
|---|---|---|
| Rules-only baseline | free | Does this need a model at all? |
| Haiku in every role | measured | Does the large model add anything on hard cases? |
| Routed: Haiku triage and workers, Opus lead | measured | The proposed design |
| NVIDIA-hosted model in every role | not run: no NVIDIA key was used; the adapter has unit tests only | Does the system carry over to another vendor's model? |
| Opus on every alert, low effort, no triage tier | measured (added after the routed run) | What routing saves |

**The open question, and what happened.** A single large model at low effort is
the simpler alternative to routing. The plan assumed it would not fit the budget
on the full set and would be measured on a subset at most. The routed run came in
under its estimate, so the single model was run on all 40 alerts instead. It
matched the routed design on every outcome at about the same cost; the README's
evaluation section and decision 2 in the decision log have the numbers.

## 12. Audit trail and case viewer

Each case stores every step: role, model, tokens, cost, tool calls and results (in
tokens), delegations, policy rules that fired, sensitive-detail requests and the
analyst's decision.

- `aml-agent trace <alert>` prints the timeline; `--reveal` restores real values.
- A Streamlit viewer shows the queue, the timeline, the disposition and narrative
  with real values restored, and approve or override buttons.

## 13. Failure handling

| Failure | Behaviour |
|---|---|
| API error, rate limit, timeout after SDK retries | `MODEL_FAILURE`, manual investigation |
| Refusal | Opus: server-side fallback, then `MODEL_FAILURE`. Haiku: `MODEL_FAILURE` |
| No submit call | one re-prompt, then `MODEL_FAILURE` |
| Tool raises | error result returned to the model |
| Tool arguments do not fit the schema | error result returned to the model |
| Submission is inconsistent (close without a reason code, escalation without a narrative) | one retry, then `MODEL_FAILURE` |
| Unknown token in a tool argument | error result returned to the model |
| Worker fails | error result to the lead, which may proceed without it |
| A live call fails while recording | the failure is recorded, so the run replays exactly; a later live run retries it |
| Unexpected error in one case | `INTERNAL_ERROR`, manual investigation; the other cases finish |
| Budget reserve reached | run stops cleanly; finished cases are kept |
| Outbound check hit | `LEAK_BLOCKED`, manual investigation |

No failure produces a close.

## 14. Layout

```
projects/aml-alert-investigator/
  README.md
  pyproject.toml
  src/amlagent/
    models.py      typed data: alert, disposition, trace
    bankdb.py      builds and reads the synthetic bank database
    features.py    risk features and hard flags
    privacy.py     token vault, field policies, scrubber, outbound check
    tools.py       tool schemas and handlers, per-role registry
    llm.py         provider interface, Anthropic, cassette, scripted, guarded, router
    nim.py         NVIDIA-hosted model provider
    budget.py      spend ledger and cap
    agent.py       bounded tool loop
    roles.py       prompts and configuration for triage, lead, workers
    workers.py     the three worker digests
    policy.py      Policy thresholds, named rules, gate
    pipeline.py    one alert end to end
    baseline.py    rules-only configuration
    store.py       cases, audit log, analyst decisions
    evals.py       scoring and report
    cli.py
  app/case_viewer.py
  scripts/         build_dataset.py, render_flow.py, render_flowchart.py
  data/            core/*.csv, alerts/, gold/, cassettes/
  docs/            design.md, discovery-notes.md, workflow-map.md, project-plan.md,
                   decision-log.md, data-protection.md, impact-estimate.md,
                   eval-report.md, agent-flow.png, agent-flowchart.png
  tests/
```

Stack: Python 3.12, uv, `anthropic`, the `openai` client as transport for the NVIDIA
endpoint (optional extra), pydantic, typer, SQLite, Streamlit, matplotlib for
diagrams, pytest, ruff. Same conventions as rfq-to-quote.

## 15. Testing

- Features and tools: fixed inputs, exact outputs.
- Privacy: tokens are stable within a case and differ across cases; field
  allow-lists; scrubber on seeded text; the outbound check blocks a planted
  identifier; re-identification round trip.
- Leak scan: every recorded request in the cassettes is scanned against every real
  identifier in the database.
- Agent loop with scripted responses: normal finish, escalation, missing submit,
  invalid submission, tool error, step limit, several tool calls in one turn, refusal.
- NVIDIA provider with a fake client: request and reply translation, unusable tool
  arguments, request cap.
- Policy: one test per rule.
- Cassette: a recorded investigation replays without a live provider; a changed
  prompt is a miss.
- Budget: the run stops before the cap; cost arithmetic including cache tokens.
- Evaluation: scoring on hand-built cases.

CI gets a second job that runs lint, tests, the leak scan and the evaluation in
replay mode, with no secret.

## 16. Deliverables

- Working code, tests and CI job.
- README with a component diagram and a decision flowchart (both PNG, rendered by
  scripts in the repo), a worked example, results and limits.
- Discovery notes, workflow map, project plan, decision log, data-protection note,
  impact estimate.
- Evaluation report for the rules baseline, and for the model configurations once
  the live runs are approved and recorded.
- A row for this project in the repository README.
