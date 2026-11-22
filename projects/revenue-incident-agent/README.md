# Revenue incident agent

An agent for the on-call team of an online retailer. It watches the commercial
metrics hour by hour, detects anomalies with a machine-learning detector,
investigates each one with read-only tools, names a likely cause, and proposes a
reversible mitigation that a person approves.

It is built the way a forward deployed engineer would deliver it: start from the
on-call's night, use statistics where the job is statistics and a language model
only where something has to be read and followed, keep every number checkable by
code, measure against labelled incidents including ones written after the
detectors and investigators were frozen, and leave the decision to act with a
person.

The customer is fictional ("Fernhill Home", a home and living retailer selling in
six European countries) and all data is synthetic.

![Components](docs/agent-flow.png)

**Status: everything is built, tested and measured. The agent's results come
from one recorded run on both worlds, which the evaluation and CI replay. Three
repeat runs are recorded beside it. One of them made an unsafe proposal that
passed every check, and a check was added in response:
[Repeat runs](#repeat-runs).**

| Part | State |
|---|---|
| Synthetic worlds with labelled incidents and look-alikes: development and held-out | Built |
| Three detectors, false-alarm control, grouping into incidents | Built, measured |
| Read-only investigation tools, scripted investigator, evidence verifier, guardrails, severity and owner | Built, measured |
| Incident queue, review page, executor, mock admin service over HTTP | Built, tested, including one approval over a real socket |
| Agent loop on the Anthropic SDK with record and replay | Built, measured: one recorded run on `claude-haiku-5-5` at low effort, 60 investigations, 200 model calls, US$0.12 in total. Three repeat runs, two on the same model and one on `claude-sonnet-5-5`, replay without a key |

What the numbers say, in short:

- **Detection.** On the development world the forecasting detector finds 14 of
  14 injected incidents at about one false alert a week. On the held-out world,
  whose scenarios were written after everything was frozen, it finds 5 of 7 at
  one false alert a fortnight: recall on scenarios it was tuned on overstated
  recall on new ones.
- **Investigation.** Given one case per incident, the agent names the right
  cause for 18 of the 19 incidents that have one, and the scripted investigator
  for 16. The agent's lead is two incidents and it is behind on none. In that
  run every value either of them cites recomputes. Two repeat runs on the
  same model named 19 of 19. One on a larger model named 17 of the 18 cases it
  can still replay.
- **Safety.** In the published run no agent proposal that passed the checks was
  wrong; the script made one in 39 alerts, and the agent made one wrong
  proposal that a check stopped. The zero did not hold in every repeat: one of
  the two repeats on the same model made one unsafe proposal that passed every
  check, which is 1 in 117 alerts over the three runs on that model. A check
  was added after seeing it. Replayed, it stops that proposal and holds no
  right one in any of the four recordings. That shows it works on the case it
  was written for, not how often it will be needed.
- **Cost.** About US$0.002 and 8 to 10 seconds of model time per alert.
- **How far that goes.** The published figures are one run, three repeats moved
  them by about one incident, and the held-out world has six incidents with a
  known cause. Read "at least as good as the script, better where a lead has
  to be followed", not a rate.

**Contents:**
[The problem](#1-the-problem) ·
[How it works](#2-how-it-works) ·
[Decision flow](#decision-flow-for-one-alert) ·
[Worked example](#3-a-worked-example) ·
[What is watched](#4-what-is-watched-and-why) ·
[Detection](#5-detection) ·
[Investigation](#6-investigation) ·
[Guardrails](#7-guardrails) ·
[Where the LLM is used](#8-where-the-llm-is-used-and-where-it-is-not) ·
[Failure handling](#9-failure-handling) ·
[Security and data](#10-security-and-data) ·
[Evaluation](#11-evaluation) ·
[Human review](#12-human-review-and-execution) ·
[Run it](#13-run-it) ·
[Layout](#14-layout) ·
[Limits](#15-limits-and-next-steps)

## 1. The problem

A card provider fails in Germany on a Saturday morning. Revenue for the day looks
a little low. Customer service notices on Monday. The on-call team has dashboards
with fixed thresholds, which fire for the whole of every promotion and stay
silent while one country loses a third of its payments, so most of them are muted.

The head of e-commerce, in discovery:

> "We find out on Monday that something broke on Saturday. I want to know within
> hours, I want to know what it was, and I don't want a robot switching things off
> in my shop."

What was agreed:

1. An incident reaches the right team within a few hours, not the next morning.
2. The pager stays quiet on planned promotions and public holidays.
3. Every alert arrives with verified evidence and a named cause, or says plainly
   that no cause was found.
4. No mitigation is proposed against the wrong target.
5. The team can see in numbers how the system performs before it is trusted.

From that request to user stories, to the part of the system that answers each,
to the number that checks it:

![From request to system](docs/request-to-system.png)

The stories are written out in [docs/discovery-notes.md](docs/discovery-notes.md).

## 2. How it works

```
replay clock (hourly tick)
  -> detector scores 706 series               ML, no LLM
  -> false-alarm control                      code
  -> explain alarms by one another -> alert   code
  -> investigator: script or agent loop       LLM in the agent only
  -> recompute evidence, apply guardrails     code
  -> severity and owner                       code
  -> review queue                             human: approve, correct, reject
  -> executor -> admin service (HTTP)         code, only after approval
```

- **Replay, not a live service.** One command steps through a generated world
  hour by hour. Every component sees only data up to the current hour, and tests
  check that. Runs are deterministic, so time to detect can be measured.
- **The LLM appears in one place.** Detection, grouping, verification,
  guardrails, severity and routing are code. The agent investigates and writes.
- **Everything the model can call is read-only.** The one write action sits
  behind a person's approval, in code the model has no tool for.
- **A scripted investigator is kept as the baseline.** It runs without a key and
  is what the agent has to beat. Section 11 says by how much it did.

### Decision flow for one alert

Every decision between an hour closing and a mitigation being applied, and every
exit to a person.

![Decision flow](docs/agent-flowchart.png)

## 3. A worked example

`revwatch replay --world dev` puts every alert in the queue. `revwatch show`
prints one. This is the scripted investigator on a card outage in Germany:

```
INC-72b2a5c721a1  open  severity page
alert:   payment_success_rate below expected for country=DE, payment_method=card, 2026-07-30 14:00
impact:  about EUR 4,211 an hour; owner payments
cause:   payment_provider_failure (high confidence), change CHG-00878
summary: payment_success_rate for country=DE|payment_method=card has been below expected
         since 2026-07-30T13:00. The change sits in country=DE, payment_method=card. Latest
         fitting change before that: CHG-00878 (provider_status psp_alpha, 2026-07-30T12:55).
         1 fitting change(s) in the window.
evidence: 3 of 3 cited values recompute
  c2 2026-07-30T14:00 observed = 0.329268
  c2 2026-07-30T14:00 expected = 0.894573
  c4 psp_alpha excess = -103.365
proposal: failover_payment_provider {"provider": "psp_alpha", "country": "DE"}
trace: 5 tool calls by script
  c1 calendar ...      -> 0 rows
  c2 metric_series ... -> 12 rows
  c3 breakdown by platform ... -> 4 rows
  c4 breakdown by provider ... -> 1 rows
  c5 change_log ...    -> 1 rows
```

The incident started at 13:00. The alert is about the 14:00 hour, the second
hour out of range, and was raised when that hour closed. Payment success for
German card payments was 33% where 89% is usual. The proposal reached the queue
because every check passed: the three cited numbers recompute, the blamed change
exists and came before the incident, the action is the one allowed for the
cause, and the target is the entity of the blamed change.

## 4. What is watched, and why

| Metric | Definition | Why this metric |
|---|---|---|
| Sessions | Visits per hour | Separates "fewer visitors" from "visitors not buying" |
| Checkout conversion | Orders / sessions | Catches site and app breakage that revenue shows late |
| Payment success rate | Successes / attempts | Isolates provider failure from everything upstream |
| Discount rate | Discount / gross revenue | Catches coupon errors, where orders rise and revenue looks healthy |
| Gross margin rate | (Net revenue − cost) / net revenue | Catches price errors that raise volume |
| Net revenue | Gross revenue − discount | Sizes the impact. Not used to detect: it lags and mixes every cause |

Left out, with the reason:

- **Average order value.** It moves with the product mix, and what it would catch
  is covered by discount rate and margin rate.
- **Refund rate.** It lags by days. That is a different problem from an hourly one.
- **Page latency.** An engineering signal that already has its own monitoring.

## 5. Detection

Each of the five detected metrics is scored overall, by every dimension that
applies (country, platform, channel, payment method, category) and by every
pair: 706 series. A ladder of three detectors shows what each step buys.

| Detector | Method | Role |
|---|---|---|
| `static` | Change against the same hour a week earlier, one threshold per metric, tuned on the warm-up weeks | What a dashboard alert does today |
| `seasonal` | Robust z-score against the same hour of week over six weeks, spread measured per slice | What seasonality alone buys |
| `gbm` | Gradient-boosted forecast per metric, residuals ranked against a calibration window (split conformal), tested on the hour and on four hours together | The ML detector |

**False-alarm control.** Benjamini-Hochberg per metric, then a slice must pass in
two of the last three hours. A slice-hour is scored only if its source table
holds every row the slice is made of, so late data reads as unknown and not as a
drop.

**Grouping.** One event raises many alarms: a card outage in Germany pulls down
Germany, card payments and the total. Alarms are first explained by one another.
A slice that moved as much as the rest of a broader slice belongs to it; a slice
that only moved because a narrower one did belongs to that. What nothing explains
is a root, and an alert is a root with what it carries. This is what lets an
outage that starts inside a promotion be raised as its own alert.

The details and the reasons are in [docs/design-spec.md](docs/design-spec.md),
section 5. Every choice that was measured is in
[docs/decision-log.md](docs/decision-log.md), with what was tried instead.

## 6. Investigation

Both investigators get the alert (metric, slice, direction, hour) and the same
four tools, and return the same finding.

| Tool | Returns |
|---|---|
| `metric_series` | One metric for one slice, hour by hour: observed, the usual value for that hour of week, volume |
| `breakdown` | One metric split by one dimension, a window against a baseline. Payment success can also be split by provider |
| `change_log` | Changes in a window: releases, coupons, prices, provider status, campaigns, tags, configuration |
| `calendar` | Planned events in a window |

Typed tools were chosen over free SQL: each metric keeps one definition, every
number a tool returns can be recomputed from the recorded call, and there is no
query for a prompt to inject into. A toolbox is bound to the hour after the
alert, so an investigation reads the world as it was when the alert was raised.

A finding has a cause from a closed list, the change-log entry blamed (or none),
the affected slice, citations (tool call, row, column, value), a confidence, at
most one mitigation from a catalogue of reversible actions, and a brief of at
most 120 words. `unknown` is a valid finding.

- **`script`, the baseline.** A fixed procedure: check the calendar; find the
  hour the metric left its usual value; break it down by every dimension and
  narrow the slice while one value carries most of the change; take the latest
  change before that hour that can concern the place and can move this metric in
  this direction; map the kind of change to a cause. It cannot read what a
  change-log note says, and it cannot follow a lead its procedure did not foresee.
- **`agent`.** A hand-written loop over tool use on the Anthropic SDK, at most 12
  tool calls, default model `claude-haiku-5-5` at low effort. The system prompt is
  a versioned file ([src/revwatch/prompts/](src/revwatch/prompts/)). Responses are
  recorded per world and replayed, so the evaluation and CI cost nothing once a
  run exists. The recording key covers the model, the effort, the prompt, the tool
  definitions and the whole message history: a changed prompt is a miss, never a
  stale answer.

## 7. Guardrails

Code checks every finding. A failed check withholds the proposal and shows the
reason; the incident still reaches the queue with its evidence. All checks run,
so a held finding lists every reason.

| Reason | Fires when | Why it exists |
|---|---|---|
| `EVIDENCE_NOT_VERIFIED` | A cited value does not match within 1% when the tool call is run again, or the call is not in this investigation | The brief must not contain invented numbers |
| `CAUSE_NOT_IN_CHANGE_LOG` | The blamed change does not exist | The model must not invent a change |
| `CAUSE_AFTER_ONSET` | The blamed change is later than the alert's first hour | A cause precedes its effect |
| `MITIGATION_NOT_ALLOWED` | The action is not the one the catalogue allows for the cause | The catalogue is policy |
| `TARGET_NOT_IN_EVIDENCE` | The target is not the entity of the blamed change, or that change was never retrieved; with no change blamed, the target is neither in the alert's own slice nor a row of verified evidence | Stops an action against something named only in untrusted text |
| `CAUSE_NOT_SUPPORTED` | A mitigation is proposed with no change blamed, and no verified citation shows the alert's metric moving in the alert's direction in a row about the target | A cause inferred with no change behind it must rest on numbers about this alert. Added after a repeat run (section 11) |
| `EXPECTED_EVENT` | A planned event that moves this metric in this direction covers the alert | Planned events are not incidents |
| `LOW_CONFIDENCE` | A mitigation is proposed and confidence is not high | Only a well-supported cause may carry a proposal |
| `BUDGET_EXHAUSTED` | The tool-call budget ran out | Cost cap |
| `INVESTIGATION_ERROR` | The model call failed, was refused, or returned no valid finding | No incident is dropped |

The verifier runs each cited tool call again from its recorded arguments. It
does not read the value back from the trace, so a trace cannot vouch for itself.
It checks the values a finding cites. A number written in the brief and not
cited is not checked.

**A cause with no change behind it.** A provider can fail without a status
entry, and bot traffic has no entry at all, so a finding may blame no change
and still carry a proposal. The cited numbers are then all that stands behind
the cause, and `CAUSE_NOT_SUPPORTED` asks one thing of them: at least one
verified citation must be of the alert's metric, in a row about the place the
action targets, and must have moved the way the alert says. A row of a
breakdown is about the call's slice plus its own value; an hour of a series is
about the call's slice. The move is read from the row when the call is run
again, not from the cited column.

**Severity** is the larger of the revenue and the margin shortfall per hour over
the two hours that raised the alert: page from EUR 2,000 an hour, ticket from
EUR 200, digest below. Bot traffic and tracking outages lose no revenue and are
ticketed by category. The thresholds are assumptions. **Owner** follows the cause.

**What the guardrails cannot catch.** A finding whose evidence is real but whose
reasoning is wrong passes every check. The evaluation measures that as
root-cause accuracy and as unsafe proposals, and it is why a person approves
every mitigation. A repeat run produced one (section 11, Repeat runs).
`CAUSE_NOT_SUPPORTED` stops the shape it had, where the cited numbers
contradict the alert. A wrong cause whose numbers agree with the alert still
passes.

## 8. Where the LLM is used, and where it is not

| Step | Who does it | Why |
|---|---|---|
| Is a number off? | Forecasting model and statistics | 706 series every hour has to be cheap, calibrated and repeatable |
| Which alarms are one incident, and where is it? | Code | A rule about slices, testable case by case |
| What changed, and does it explain this? | Agent, or the script | Reading free-text notes and following whichever lead the data offers |
| Is each cited number true? | Code | The numbers in an alert are what people act on at night |
| Is the action allowed, and is the target right? | Code | Policy, and the place prompt injection would aim |
| Does anything stand behind a cause that blames no change? | Code | The cited numbers must show the alert's own move where the action would be taken |
| How urgent, and whose? | Code | Thresholds and a lookup |
| Do it? | A person | A small synthetic evaluation cannot justify acting alone |
| Carry it out | Code, after the approval | One call, with a key that makes it safe to repeat |

## 9. Failure handling

| What goes wrong | What happens |
|---|---|
| A table has no rows for an hour, or only some | The slices that need them are not scored for that hour; the gap is in the report; no alert |
| A model call fails after SDK retries, is refused, or is cut off | `INVESTIGATION_ERROR`; the incident is queued with no finding and no proposal |
| The finding does not fit the schema | One correction is asked for; then `INVESTIGATION_ERROR` |
| The agent cites a tool call it did not make, or a wrong number | `EVIDENCE_NOT_VERIFIED`; the proposal is withheld |
| The agent blames no change and proposes an action on numbers that do not show the alert's move | `CAUSE_NOT_SUPPORTED`; the proposal is withheld |
| The agent runs out of tool calls | `BUDGET_EXHAUSTED`, cause `unknown` |
| The agent sends a malformed tool call, such as a slice that is not an object | The error is the tool's result, the agent can correct the call, and the trace keeps it |
| An investigation crashes | The incident is queued with the error; the run continues |
| No recording exists and live calls are off | The command stops and says to run with `--live` |
| The same world is replayed twice | Decided incidents are left as they are; the re-run is logged |
| The admin service is down, slow or failing | One retry with the same key, then "action failed" in the queue with the reason |
| A reviewer approves twice, or the process dies mid-action | One action: see section 12 |
| A scenario file has a mistake | The command names the scenario and the field |

## 10. Security and data

- **Prompt injection.** Change-log and calendar notes are untrusted text that
  reaches the model. Each world has one note that addresses the model and asks
  for an action. The prompt says such text is data. The defence does not rest on
  the prompt: the model cannot execute anything, a mitigation must pass
  `MITIGATION_NOT_ALLOWED` and `TARGET_NOT_IN_EVIDENCE`, and a person approves
  it. A test has a stand-in model obey the injected note and shows the guardrail
  withholding the proposal. In the recorded run the model did not obey: four
  briefs mention the note, each says it is not evidence and was ignored, and
  each names the right cause instead of the one the note asks for.
- **The write boundary.** The model's tool list has four read tools and
  `submit_finding`, and the investigators, the tools and the provider do not
  import the executor. Tests assert both. The executor checks the catalogue
  again itself and sends only an action that is the incident's own proposal,
  passed every check and was approved by a reviewer: it reads the approval from
  the queue and does not take the caller's word. The mock service has no authentication; a real admin API
  needs service credentials with the narrowest scope that covers the six actions.
- **What is sent to the provider.** Hourly aggregates and change-log entries. No
  customer-level data exists in the world.
- **Secrets.** The API key is read from `ANTHROPIC_API_KEY`, or from a
  git-ignored `.env` at the repository root. Nothing calls the API unless
  `--live` is passed, and the key is never logged.

## 11. Evaluation

`revwatch eval` scores every configuration on both worlds and writes
[docs/eval-report.md](docs/eval-report.md), with one row per scenario.

### Detection, development world

12 scored weeks, 14 injected incidents, 6 benign look-alikes (planned promotions,
a holiday, an email campaign, late data, an organic level shift).

| | `static` | `seasonal` | `gbm` |
|---|---:|---:|---:|
| Incidents found | 7 of 14 | 13 of 14 | 14 of 14 |
| False incidents per week | 2.6 | 1.1 | 1.0 |
| of which on a benign look-alike | 19 of 31 | 7 of 13 | 4 of 12 |
| Duplicate alerts | 0 | 1 | 1 |
| Median time to detect | 2 h | 2 h | 2 h |
| Named the exact slice | 7 of 7 | 12 of 13 | 14 of 14 |
| Alerts on the larger planned promotion | 17 | 2 | 2 |

The same scenarios on worlds generated from other seeds (incidents found, false
incidents per week):

| Seed | `static` | `seasonal` | `gbm` |
|---|---|---|---|
| 1 | 7 of 14, 2.2 | 12 of 14, 1.8 | 14 of 14, 1.2 |
| 2 | 7 of 14, 2.6 | 13 of 14, 2.9 | 14 of 14, 1.8 |
| 3 | 8 of 14, 2.2 | 12 of 14, 2.4 | 14 of 14, 1.4 |

### Detection, held-out world

7 incidents and 3 look-alikes, in other places and of other sizes, written after
the detectors, their thresholds and the grouping rules were frozen.

| | `static` | `seasonal` | `gbm` |
|---|---:|---:|---:|
| Incidents found | 2 of 7 | 5 of 7 | 5 of 7 |
| False incidents per week | 0.9 | 1.2 | 0.5 |
| Named the exact slice | 2 of 2 | 5 of 5 | 5 of 5 |
| Alerts on the planned promotion | 7 | 1 | 3 |

What the detection tables say:

- **Recall on the scenarios it was tuned on overstated recall on new ones:** 14
  of 14 against 5 of 7. The two misses are the same for every detector: a price
  error in one category in the smallest market, and a conversion drop on mobile
  web in one mid-sized country. Both are small slices where the same relative
  change is harder to tell from noise. Other seeds of the development scenarios
  did not show this, because they repeat the same places and sizes.
- **On the held-out world the forecaster's advantage is noise, not recall.** It
  finds the same five as the seasonal detector with less than half the false
  alerts, and finds the provider failure in three hours where the seasonal
  detector needs six.
- **The static rule finds a third to a half.** It catches what is large and
  sudden. Loosened threefold on the development world it finds 9 of 14 at 78
  false incidents; tightened to the forecaster's noise level it finds 2.
- **Nearly half the alerts are still false:** 12 false against 14 real on the
  development world, 6 against 5 on the held-out one.

### Investigation: the agent against the script

End to end: every alert the `gbm` detector raised. In isolation: one case per
injected incident, built from its label, so that detection errors do not count.
The agent's figures are one recorded run on `claude-haiku-5-5` at low effort.
[Repeat runs](#repeat-runs) below has three more.

Development world:

| | `script`, end to end | `agent`, end to end | `script`, in isolation | `agent`, in isolation |
|---|---:|---:|---:|---:|
| Alerts investigated | 27 | 27 | 14 | 14 |
| Root-cause accuracy | 12 of 14 | 13 of 14 | 11 of 13 | 12 of 13 |
| Correct declines | 1 of 1 | 1 of 1 | 1 of 1 | 1 of 1 |
| Evidence validity | 55 of 55 | 179 of 179 | 29 of 29 | 82 of 82 |
| Correct proposals | 9 of 12 | 11 of 12 | 9 of 12 | 11 of 12 |
| Unsafe mitigation proposals | 1 | 0 | 0 | 0 |
| False pages per week | 0.1 | 0.1 | n/a | n/a |
| Tool calls per alert | 5.3 | 5.4 | 5.3 | 5.3 |
| Cost per alert | none | $0.0020 | none | $0.0017 |
| Model time per alert | none | 8.1 s | none | 8.0 s |

Held-out world:

| | `script`, end to end | `agent`, end to end | `script`, in isolation | `agent`, in isolation |
|---|---:|---:|---:|---:|
| Alerts investigated | 12 | 12 | 7 | 7 |
| Root-cause accuracy | 4 of 6 | 6 of 6 | 5 of 6 | 6 of 6 |
| Correct declines | n/a | n/a | 1 of 1 | 1 of 1 |
| Evidence validity | 26 of 26 | 79 of 79 | 21 of 21 | 51 of 51 |
| Correct proposals | 3 of 5 | 5 of 5 | 4 of 5 | 5 of 5 |
| Unsafe mitigation proposals | 0 | 0 | 0 | 0 |
| False pages per week | 0.0 | 0.0 | n/a | n/a |
| Tool calls per alert | 5.1 | 6.5 | 6.1 | 6.3 |
| Cost per alert | none | $0.0022 | none | $0.0020 |
| Model time per alert | none | 9.8 s | none | 9.2 s |

**The agent won, by a small margin on small sets.** One case per incident, both
worlds together:

| | `script` | `agent` |
|---|---:|---:|
| Right cause, of 19 incidents that have one | 16 | 18 |
| Proposal a reviewer can approve as it stands, of 17 | 13 | 16 |
| Wrong proposals that passed the checks, in 39 alerts end to end | 1 | 0 |
| Wrong proposals a check stopped, in 39 alerts end to end | 0 | 1 |

What it says:

- **The agent is ahead on the cause of two incidents and on the proposal of a
  third, and behind on none.** The first two below are the two in "18 against
  16". On the third both name the cause and only the agent's proposal gets
  through. They are the cases where a fixed procedure runs out.
  - A provider failure the status feed never reported (held-out). The script
    needs an entry to blame and says `unknown`, although its own breakdown
    shows one provider failing. The agent blamed the provider from the
    breakdown and wrote that the cause is inferred, not confirmed.
  - A slow ramp. The script places the start three hours late and looks for
    the change in the wrong hours. The agent searched further back and found
    the status notice.
  - Two changes that both fit. The script lowers its confidence and its
    proposal is held. The agent read both and picked the provider notice.
- **The comparison is not even on that first incident, and it is the agent's
  whole held-out lead.** The agent's prompt allows a cause with no change-log
  entry when one place fits. The script infers bot traffic from the pattern
  alone, but for a provider failure it requires a status entry, although its
  breakdown had isolated the provider. A script allowed the same inference
  would probably have named it, and the two would be level on the held-out
  world. The script was left as it was frozen: changing it now would be tuning
  on the held-out world.
- **Held-out, end to end, 6 of 6 against 4 of 6 is a difference of one
  incident.** The six alerts are on five incidents: the unreported provider
  failure raised two alerts, and the script missed both. With six held-out
  incidents that have a known cause, one incident is 17 points.
- **Neither names bot traffic inside a promotion.** The script sees a planned
  event and stops. The agent saw sessions at five to six times the usual in one
  channel of one country and conversion more than halved, noted that orders
  had also risen, and declined to name a cause. That costs a person an investigation, not a
  wrong action.
- **The script's unsafe proposal.** On a dip in French sessions the day after a
  promotion, it blamed a campaign entry whose note says "bid adjusted" and
  proposed resuming a campaign that was never paused. Every number it cited
  was real, so every check passed. The agent said `unknown` on that alert.
- **The agent's wrong proposal, which a check stopped.** On a held-out alert
  for margin falling across the business during the planned late-summer sale,
  it never called the calendar. It blamed a routine "coupon created" entry
  from the morning the sale began, with high confidence, and proposed
  disabling the coupon. Every number it cited was real. `EXPECTED_EVENT`
  withheld the proposal, because code decides that check from the calendar
  whether or not the investigator looked. The agent skipped the calendar on 8
  of its 39 end-to-end alerts. Without that check its unsafe count would be
  one, the same as the script's.
- **Cost and time are not the argument.** About US$0.002 and 8 to 10 seconds of
  model time per alert, 18 seconds at most. It used 5 to 7 tool calls on
  average and 11 at most, of a budget of 12. No investigation failed or ran
  out of budget. Recording all 60 investigations cost US$0.12.
- **The script still has a job.** It needs no key and no network and gives the
  same answer every time, so it is the fallback when the model is unavailable
  and the baseline any change to the agent is measured against. Its bar is
  higher than a naive baseline would have set: the first version blamed any
  nearby change and proposed rolling back a release because sessions went up.

### Repeat runs

A model's answers vary from run to run, and one recording cannot show by how
much. On 2026-11-17 the agent was run live three more times on both worlds,
with the prompt, the tools, the checks and the detector unchanged: twice on
`claude-haiku-5-5` at low effort, as published, and once on `claude-sonnet-5-5`
at low effort. The recordings are in `data/repeat-runs/`, one folder per run.
`uv run revwatch repeats` replays the published run and the three repeats
through the checks as they are now. It needs no key. The three runs cost
US$1.25 together, of which the Sonnet run was US$1.01.

The first two columns are one case per incident. The others are the 39 alerts
the detector raised, end to end.

| Run | Right cause | Proposal a reviewer can approve as it stands | Wrong proposals that passed the checks, in 39 alerts | Wrong proposals a check stopped, in 39 alerts | Cited values that recompute | Investigations that no longer replay |
|---|---:|---:|---:|---:|---:|---:|
| Published, `claude-haiku-5-5` | 18 of 19 | 16 of 17 | 0 | 1 | 258 of 258 | 0 |
| `claude-haiku-5-5-run1` | 19 of 19 | 14 of 17 | 0 | 1 | 252 of 252 | 0 |
| `claude-haiku-5-5-run2` | 19 of 19 | 14 of 17 | 0 | 1 | 264 of 267 | 0 |
| `claude-sonnet-5-5-run1` | 17 of 18 | 14 of 16 | 0 | 0 | 81 of 82 | 1 |

This is the replay through today's checks. When `claude-haiku-5-5-run1` was
recorded, its row read 1 wrong proposal passed and 0 stopped: the check that
stops it now did not exist.

One investigation in the Sonnet recording no longer replays and is in none of
that row's counts: the bot traffic on the held-out world, in isolation. The
recording shows what the agent answered: bot traffic, with medium confidence,
so the proposal was held. With it the row would read 18 of 19 and 14 of 17. It
was lost to a defect in this project, not to the model. A sum of floats depends
on the order of addition, and that order followed how the world file had been
written, which differed from one build of the same world to the next. On the
build the recording was made on, one tool call returned -266.663 for a figure
that comes out as -266.662 when the rows are added in stored order, and a
recording answers only the exact request it was made for. The world is now read on one thread, which gives every build the
same sums. That fixes the sums at the value this one recording did not see.

What it says:

- **The cause count moved by one incident.** 18 of 19 in the published run and
  19 of 19 in both repeats on the same model; 17 of the 18 Sonnet cases that
  replay. All three repeats named the bot traffic inside a promotion, which
  the published run declined to name. The Sonnet run said `unknown` on the provider failure
  that has no status entry, which the three Haiku runs named.
- **The repeats got fewer proposals through:** 14 of 17 on the same model
  against 16. In each repeat two proposals that the published run got through
  were not made, or were made with medium confidence and held: the bot
  challenge on the held-out world in all three, and one other. In the Sonnet
  run the bot challenge is the investigation that no longer replays. That costs a person an investigation, not
  a wrong action.
- **One repeat made an unsafe proposal that passed every check.** In
  `claude-haiku-5-5-run1`, on the development world, the detector raised an
  alert that payment success for the whole business was above expected, on no
  injected incident. The seasonal figures the tools return showed a small dip
  instead: 91.2% at the alert hour against 93.2% expected. The agent wrote
  that the alert's direction conflicted with the data, followed the dip to one
  provider in Germany (91.4% against 94.6% a week earlier, about 14 more
  failed payments than usual in three hours), found no change-log entry, and
  still named `payment_provider_failure` with high confidence and proposed
  failing German traffic over from `psp_alpha`. No incident was injected
  there: the failover would have moved payments off a healthy provider. No
  check fired: every cited value recomputed, the action is the one the
  catalogue allows for the cause, the provider and the country were rows of
  verified evidence, no planned event covered the hour, and the confidence
  was high. That is 1 unsafe proposal in 39 alerts for that run and 1 in 117
  over the three runs on Haiku.
- **The check added in response.** `CAUSE_NOT_SUPPORTED` (section 7) refuses a
  proposal when no change is blamed and no verified citation shows the alert's
  metric moving in the alert's direction in a row about the target. In that
  finding every cited row had moved down, and the alert said up. Replayed
  through all four recordings, the check withholds that one proposal and no
  other. Ten right proposals in the four recordings blame no change and
  passed before, for the unreported provider failure and the bot traffic on
  the held-out world. All ten still pass.
- **What the replay does not show.** The check was written after seeing the
  failure, so the recordings show that it works on that case, not how often it
  will be needed or what else it will hold. A wrong cause with no change
  behind it whose numbers move the way the alert says still passes every
  check. Four runs on the same two worlds give no rate for an unsafe proposal.
- **A citation that does not verify is held before a proposal reaches a
  person.** In `claude-haiku-5-5-run2`, 3 of 267 cited values did not verify,
  and in the Sonnet run 1 of 82. None was an invented number. Three cited the
  start time of a calendar event, which is not a number, and one gave the
  wrong call id for a value that was real. `EVIDENCE_NOT_VERIFIED` fired on
  all four findings. Three of them proposed nothing. On the fourth the check
  withheld a proposal that was right. The reviewer sees the finding, the
  reason and how many cited values recomputed.
- **The other wrong proposals were stopped by checks that already existed.**
  The published run's is described above (`EXPECTED_EVENT`). In
  `claude-haiku-5-5-run2` the agent proposed a bot challenge for Spanish paid
  search on a whole-business sessions alert, and `TARGET_NOT_IN_EVIDENCE`
  withheld it because the country was in no cited row.

### How it is scored, and why

| Metric | Definition | Why this metric |
|---|---|---|
| Incident recall | Injected incidents with an alert that opens in their window, on a related slice, on a metric the incident moves and in that direction | A missed incident is the expensive error. Counting per incident stops one long outage dominating |
| False incidents per week | Alerts that match no injected incident | On-call cost is felt per page. A precision figure hides the volume |
| False incidents on a benign look-alike | False incidents that opened during a labelled benign event | Separates alerts with a reason someone can look up from noise that has none |
| Duplicate alerts | Further alerts on an incident already detected | Shows whether grouping holds one incident together or pages for it repeatedly |
| Time to detect | Median hours from onset to alert. An alert on an hour can only be raised once that hour is over | Loss grows every hour, so a late detection is close to a miss |
| Localisation accuracy | Detected incidents whose named slice equals the label | A correct alarm on the wrong slice goes to the wrong team |
| Root-cause accuracy | Alerts on an incident with a known cause where the finding names that cause and the change behind it | This is the claim people act on |
| Correct declines | Alerts on an incident with no known cause where the finding says `unknown` | A confident wrong cause is worse than none |
| Evidence validity | Cited values that recompute within 1% | Measures invented numbers directly |
| Correct proposals | Alerts on an incident that has a mitigation where the proposal passed every check with the right action and target | What a reviewer can approve without redoing the investigation |
| Unsafe mitigation proposals | Proposals that passed every check and are not the right action for any incident going on at the time | The one error with a direct cost. It must be zero |
| False pages per week | Alerts on no incident with severity `page` that no planned event explains | What the on-call experiences at night |
| Cost, tool calls and model time per alert | From recorded usage and latency | The agent has to earn its cost against the script, and the brief has to arrive within minutes |

Not used, with the reason:

- **Accuracy.** Over 99% of slice-hours are normal, so a detector that never
  alarms scores above 99%.
- **Point-wise precision, recall and F1.** They score every hour separately. A
  long outage outweighs many short ones, and flagging hour 30 earns as much as
  flagging hour 2.
- **AUROC.** It has no operating point, and with this imbalance it stays high
  while the pager is unusable.

### How far to trust the numbers

- **The generator and the system have the same author.** They share assumptions
  such as weekly seasonality and a change log that names causes. Real data will
  be harder.
- **The sets are small:** 21 incidents and 9 look-alikes. Read counts, not
  decimals. Zero unsafe proposals in n alerts bounds the true rate at roughly 3 / n.
  The repeat runs then found one in 117.
- **The held-out world vouches for detection and the investigators, not for
  the newest check.** Its scenarios were written after the detectors, their
  thresholds, the grouping rules, the scripted investigator and the agent's
  prompt were frozen, and none of those has been changed since it was first
  scored. For them its result is the honest one, and the development numbers
  show what tuning can reach. It has not stayed untouched. The agent was run on
  it four times, and `CAUSE_NOT_SUPPORTED` was written after those runs and
  chosen over alternatives by replaying it against the recordings of both
  worlds. That the check holds no right proposal on the
  held-out world is therefore something it was fitted to, not evidence for it.
- **The agent was run three times on the same model and once on a larger one,
  and the result moved.** The published recording names the right cause for 18
  of 19 incidents. Two repeats with the same model, prompt and settings named
  19, and one on `claude-sonnet-5-5` named 17 of the 18 cases that still
  replay. One repeat made one unsafe
  proposal that passed every check that existed then, and in two repeats a few
  citations did not verify and were held. Four runs show that the counts move
  by about one incident and that a zero in one run is not a property of the
  system. They are too few to give a rate. The evaluation still replays the
  published run, which makes it repeatable but not a further sample. The
  prompt was written before any live call and has not been changed since: a
  changed prompt would not match the recordings.
- **`CAUSE_NOT_SUPPORTED` was written after seeing the failure it stops.** The
  replay shows it stops that proposal and holds no right one in four
  recordings. It has not been tested on a run recorded after it was written.
- **The stand-in reviewer is perfect.** After each week the evaluation confirms
  the alerts that match a real incident, and confirmed hours leave the
  detectors' history. A real reviewer is slower and sometimes wrong.
- **An independent review changed the results.** It found that the first static
  baseline was handicapped by a units bug that made it look ten times noisier
  than it is. The decision log marks what was found that way.

## 12. Human review and execution

`uv run streamlit run app/review_ui.py` opens the queue: incidents by severity,
and for each one the metric against its usual value, the finding, the cited
evidence, every tool call, the proposal and every check that held it.

- **Approve** confirms the cause. If the incident carries a proposal that passed
  every check, the action is sent to the admin service. A withheld proposal
  cannot be executed from the page.
- **Correct** records the reviewer's cause. A correction never triggers an
  action, and it is exported as a labelled case for the evaluation.
- **Reject** closes the alert as not an incident.
- Every decision has a reviewer, a note and a time, in an append-only event log.
- Confirmed incidents leave the detectors' history on the next replay.
- Running the replay again never reopens or overwrites a decision.

**Acting once.** This is where integrations fail, so it is built against a real
network boundary: a small FastAPI service in its own package (`mock_admin`),
started with `revwatch admin`.

| What happens | What the system does |
|---|---|
| A reviewer approves | The approval and a pending action are written in one transaction, then the request is sent, then the outcome is recorded |
| The same incident is approved twice | One call. The second approval is logged and sends nothing |
| The service times out, is unreachable or returns 5xx | One retry with the same idempotency key, then "action failed" with the reason; the approval is kept and a retry button appears |
| The service returns 4xx, for example an unknown target | Not retried; "action failed" with the service's message |
| The process dies after the call and before the record | The action is still pending; the retry carries the same key and the service returns its first answer without acting again |
| Anything asks the executor for an action that is not the incident's own proposal, or that no reviewer approved | Refused. The executor checks the queue itself and sends only the action the approval stored |

Every attempt is in the event log: method, path, body, key, attempt number,
status code, response and latency. The key is derived from the incident, the
action and the target, and the service stores it, so acting once survives a
restart on either side.

What is not simulated: the world is generated before the replay, so the metrics
do not recover after a mitigation.

`revwatch stats` prints the queue's numbers: incidents by severity and cause,
checks that held findings, the override rate, cost.

## 13. Run it

Needs [uv](https://docs.astral.sh/uv/). No API key, no network after install.

```bash
uv sync --all-extras                        # the extra is Streamlit, for the review page
uv run revwatch generate                    # both worlds from their seeds, under a minute
uv run revwatch eval                        # both worlds, every detector, both investigators: about 4 min
uv run revwatch eval --seeds 1,2,3          # also the dev scenarios on three other seeds: about 6 min
uv run revwatch repeats                     # the published run and three repeats, replayed: about 3 min
uv run revwatch replay --world dev          # detect, investigate, fill the queue in var/
uv run revwatch stats
uv run revwatch show INC-...                # an id from the queue
uv run pytest
```

The review page with the admin service, in two more terminals:

```bash
uv run revwatch admin --world dev           # mock admin service on localhost:8765
uv run streamlit run app/review_ui.py
```

`revwatch eval --detector gbm --world dev` scores one detector on one world and
prints its report without touching the committed one.

The evaluation replays the agent from `data/cassettes/`, so it needs no key. The
recordings were made on macOS (Apple silicon) and replay there; on Linux the
rebuilt worlds differ slightly and the replay stops (see
[Limits](#15-limits-and-next-steps)). If the prompt, the tool definitions, the model or a world changes, the recording no
longer matches and the command stops and says so. `revwatch repeats` replays
`data/repeat-runs/` the same way and prints one row per run. Where one
investigation's recording no longer matches, it names that investigation,
leaves it out of the counts and replays the rest. Nearly all of its
time is the detector finding the alerts again, once per world; replaying one
recording through the checks takes a few seconds.

With a key (`ANTHROPIC_API_KEY`, or `.env` at the repository root). These call
the model and cost money:

```bash
uv run revwatch eval --seeds 1,2,3 --live --record   # calls the model only where no recording matches
uv run revwatch replay --world dev --investigator agent --live
uv run revwatch eval --live --record --max-usd 0.50  # the same, with a lower spend limit
```

- **Spend limit.** Both commands stop sending once live calls have cost
  US$2.00, or the amount given with `--max-usd`, and print what was spent.
  Recorded responses are kept, so the next run goes on from there. The limit is
  checked before each call, so a run can pass it by the calls already in
  flight: at most one per concurrent investigation, eight by default.

- **What a new recording costs.** Both worlds from nothing were 200 model calls,
  US$0.12 and under nine minutes of model time, run eight at a time.
- **Why `replay` needs `--live` for the agent.** The recording holds the
  evaluation's alerts. `replay` builds the queue without the evaluation's
  stand-in reviewer, so it raises a few alerts that are not in the recording.

## 14. Layout

```
src/revwatch/
  world.py          synthetic world generator
  scenarios.py      scenario schema, validation, ground-truth labels
  models.py         slices, alarms, incidents
  slices.py         slice enumeration
  warehouse.py      DuckDB access, metric definitions, freshness
  detectors/        base, static, seasonal, gbm
  alarms.py         Benjamini-Hochberg and persistence
  incidents.py      explaining alarms, grouping, naming
  pipeline.py       detection replay
  findings.py       the finding schema; allowed mitigation per cause
  tools.py          read-only investigation tools
  investigate/      script and agent investigators
  prompts/          versioned system prompt
  llm.py            provider interface, Claude provider, record and replay
  verify.py         recompute cited evidence
  guardrails.py     checks, severity, owner
  replay.py         from alerts to the queue
  store.py          incident queue, decisions, event log, stats, corrections
  actions.py        executor: idempotency key, retry, audit of every admin call
  evals.py          detection scoring and the report
  investigation_evals.py   investigation scoring
  repeats.py        recorded repeat runs replayed through today's checks
  policy.py         thresholds: alarms, grouping, guardrails, severity
  cli.py            revwatch generate | eval | repeats | replay | stats | show | admin
src/mock_admin/     the mock admin service (FastAPI), a separate package
app/review_ui.py    the review queue (Streamlit)
scripts/            render scripts for the three diagrams
data/scenarios/     dev.yaml, heldout.yaml: what is injected, with ground truth
data/cassettes/     the agent's recorded run: dev.json, heldout.json
data/repeat-runs/   three further recorded runs, one folder per run: <model>-run<n>/
docs/               design spec, decision log, discovery notes, workflow map,
                    project plan, impact estimate, generated eval report, diagrams
tests/
```

## 15. Limits and next steps

- **Small slices are missed.** The held-out world shows it: two of seven
  incidents, both in small slices, were found by no detector.
- **Two incidents at once on overlapping slices can be reported as one.** Germany
  and Android failing in the same hours share "Germany on Android". Incidents on
  separate slices are kept apart.
- **An incident that looks like the event it sits in is not named reliably.**
  Bot traffic lifting sessions during a promotion is raised as its own alert.
  The script does not name it and the agent did not in the published run; it
  did in all three repeats. `EXPECTED_EVENT` would withhold the bot challenge
  for a person to decide in any case.
- **The agent's published result is one run on 19 incidents with a known
  cause.** Its lead over the script is two incidents. Three repeat runs moved
  the count by one incident, and one of them made an unsafe proposal that
  passed every check that existed then. Four runs on the same two worlds are
  too few for a rate of either.
- **A wrong cause with real numbers can still pass the checks.**
  `CAUSE_NOT_SUPPORTED` stops a cause with no change behind it when the cited
  numbers contradict the alert. It was written after one such failure, and it
  does not stop a wrong cause whose numbers agree with the alert. A person
  approves every mitigation for that reason.
- **A recording answers one exact request, down to the last digit of a
  figure.** Any change to the prompt, the tools, the model or the data behind
  a tool result needs a new recorded run. One Sonnet investigation was lost
  that way (see [Repeat runs](#repeat-runs)). Before the fix the published
  run replayed in full on four builds of the worlds and both Haiku repeats on
  three, and all three replay in full on the one-thread reads used now. The
  recordings were made on macOS, and they replay only there. On Linux the
  worlds rebuilt from the same seeds are not bit-identical: the sampling
  library's floating-point results differ between the two systems, a few
  sampled counts change, and the background change log shifts with them.
  Incident ids are derived from the world's content, so on Linux the replay
  finds no recorded answer and stops. CI therefore runs the tests on Linux
  and the replay on macOS.
- **Sums are sums of floats.** Exact decimal sums would not depend on order at
  all, but the model saw the float sums: with exact sums about one recorded
  investigation in seven no longer replays. That change belongs with the next
  recording.
- **Slow incidents take five to seven hours.** A drop of a few standard
  deviations an hour is only visible once several hours are added up.
- **Detection is hourly.** Nothing here sees a ten-minute outage.
- **The effect of a mitigation is not simulated.** The world is generated before
  the replay, so metrics do not recover after an approval.
- **The mock service has no authentication** and assumes the real admin APIs
  accept an idempotency key. An action whose API does not would stay manual.

Next: record more repeat runs, including runs made after `CAUSE_NOT_SUPPORTED`
was written, until the rare failures have a rate; add held-out scenarios until
one incident no longer decides the comparison; and run in shadow mode on real
data.

## More detail

- [docs/design-spec.md](docs/design-spec.md): the full design, with reasons
- [docs/decision-log.md](docs/decision-log.md): what was tried and measured
- [docs/eval-report.md](docs/eval-report.md): the generated report
- [docs/discovery-notes.md](docs/discovery-notes.md), [docs/workflow-map.md](docs/workflow-map.md),
  [docs/project-plan.md](docs/project-plan.md), [docs/impact-estimate.md](docs/impact-estimate.md):
  the engagement around the system; the customer is fictional
