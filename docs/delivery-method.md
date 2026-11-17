# How these projects are delivered

The projects in this repository are built in the same eight steps. They are the steps I
would follow with a customer. The customers here are fictional, and each README says
so. Each step below links to the project that shows it best.

## 1. Start from the request, in the client's words

A request arrives as a wish, not as a specification. Discovery turns it into three to
five user stories, and each story later gets a part of the system that answers it and
a number that proves it.

Example: the [user stories](../projects/rfq-to-quote/docs/discovery-notes.md#user-stories)
of RFQ-to-quote, and the map at the end of
[its problem section](../projects/rfq-to-quote/README.md#1-the-problem) that follows
each story through to a measured result.

## 2. Map the work before and after

Before any design, I write down who does what today, where the time goes and which
errors happen. The same map is then drawn with the system in it, including who decides
what and what still reaches a person.

Example: the [workflow map](../projects/rfq-to-quote/docs/workflow-map.md) of
RFQ-to-quote.

## 3. Use a model only where it earns its place

Reading messy language and following an open-ended lead are work for a model. Prices,
policy and arithmetic are rules the business already has, and they belong in code its
owners can read. Where statistics does the job, statistics does it.

Examples: in RFQ-to-quote the model reads the request and names the products, and
[pricing and approval are plain code](../projects/rfq-to-quote/README.md#6-where-the-llm-is-used-and-where-it-is-not).
In the revenue incident agent a forecasting model finds the incident, the agent
investigates it, and
[code recomputes every number the agent cites](../projects/revenue-incident-agent/README.md#8-where-the-llm-is-used-and-where-it-is-not).

## 4. Build the evaluation before trusting anything

Each project has a labelled set, a baseline that uses no model, a part held back from
tuning, and one command that scores everything. The worst error is named and counted
on its own line.

Examples: the revenue incident agent's detector finds 14 of 14 incidents on the world
it was tuned on and 5 of 7 on a
[held-out world](../projects/revenue-incident-agent/README.md#11-evaluation) written
after everything was frozen. The second number is the one to believe. The RFQ matcher
is also [scored on a public benchmark](../projects/rfq-to-quote/docs/eval-outside-data.md)
that I did not write, where it reaches F1 71.7%.

## 5. Put the rules in code, with names

What may happen automatically is decided by named rules, not by a model's judgement.
In RFQ-to-quote every rule runs every time, so a quote that is held lists every reason.
Data gets the same treatment as decisions.

Examples: the [guardrails](../projects/rfq-to-quote/README.md#5-guardrails) of
RFQ-to-quote, each with the reason it exists, and the
[privacy gateway](../projects/aml-alert-investigator/README.md#5-handling-bank-data)
of the AML alert investigator, behind which the model works on pseudonymised data. A
leak scan of everything sent to a model found no real identifier.

## 6. Keep a person where a mistake costs money

The reviewer sees the source next to the draft and the reasons it reached them. In
RFQ-to-quote, what the reviewer corrects becomes a new evaluation case, so a mistake
found once is tested from then on.

Examples: the
[review queue and its feedback loop](../projects/rfq-to-quote/README.md#10-human-review-and-the-feedback-loop)
in RFQ-to-quote, and the
[case viewer](../projects/aml-alert-investigator/README.md#12-human-review) of the AML
alert investigator, where a person approves every outcome.

## 7. Roll out in stages, each with a gate

Shadow mode comes first, then assisted mode, then automatic handling of the simplest
class of cases. Each stage has a number it must reach and a sponsor who decides. The
expected benefit is written as a model with its assumptions stated, so the client can
change an assumption and see the result move.

Examples: the
[rollout plan and go-live gates](../projects/rfq-to-quote/README.md#12-rollout-plan-and-go-live-gates)
and the [impact estimate](../projects/rfq-to-quote/docs/impact-estimate.md) of
RFQ-to-quote.

## 8. Say what the numbers do not show

Headline results on small sets are given as counts. A design that did not win is
reported as not having won, and a path that was never run is labelled as never run.

Examples: RFQ-to-quote's published run turned out to be the best of six, and its
[evaluation](../projects/rfq-to-quote/README.md#9-evaluation) says so and shows the
rule that was added in response. The AML alert investigator was designed around routing between models, and
its [evaluation](../projects/aml-alert-investigator/README.md#10-evaluation) says that
on this set routing did not beat a single model. Its
[second model vendor](../projects/aml-alert-investigator/README.md#7-a-second-model-vendor)
is unit-tested but was never run against the live endpoint, and the README says that too.
The revenue incident agent has a section on
[how far to trust the numbers](../projects/revenue-incident-agent/README.md#how-far-to-trust-the-numbers).

## The standard behind the steps

[Project standard](project-standard.md) lists what every project contains as a result:
the folder layout, the README order, the three diagrams, the delivery documents and
the evaluation rules.
