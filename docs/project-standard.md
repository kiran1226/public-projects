# Project standard

What every project in this repository contains. A project is finished when everything
on this page is true of it. [RFQ-to-quote](../projects/rfq-to-quote/) is the reference.

## Folder layout

```
projects/<name>/
  README.md
  pyproject.toml     a uv project: Python 3.11 or later, ruff at line length 100
  uv.lock
  .python-version
  src/<package>/     the system, one module per step of the flow
    llm.py           where a model is called: provider interface, record and replay
    evals.py         scoring and the report
    cli.py           one command-line entry point
  app/               the page a person uses: review queue, case viewer
  scripts/           render scripts for the three diagrams, and dataset builders
                     where a project has them
  data/              inputs, labels and recorded model responses
  docs/              diagrams, evaluation report, delivery documents
  tests/
```

Each project is self-contained: it has its own dependencies and lock file, and it
shares no code with its neighbours.

## README order

Every README has these parts, in this order:

- **Title and summary.** What the system does and for whom, then how it was built.
- **Status.** What is built, what is measured, the headline numbers, and what those
  numbers do not show.
- **Contents.**
- **The problem.** The client and the work as it is done today. It ends with the
  request-to-system map.
- **How it works.** The steps of the flow, and which of them call a model.
- **Decision flow.** The flowchart for one request.
- **A worked example.** One case followed through the system.
- **Guardrails.** The named rules and what each one does.
- **Failure handling.**
- **Evaluation.** The sets, the configurations, the results and how far to trust them.
- **Human review.**
- **Run it.** The commands that need no key first, then the live ones with what they
  cost or the spend cap they run under.
- **Layout.**
- **Limits and next steps.**
- **More detail.** Links to the delivery documents.

### Sections a project adds

A README adds sections between these where its subject needs them:

| Section | Where |
|---|---|
| Where the model is used and where it is not | RFQ-to-quote, revenue incident agent |
| Security and data | RFQ-to-quote, revenue incident agent. The AML alert investigator has "Handling bank data" in its place |
| Rollout, impact and integration | RFQ-to-quote, AML alert investigator. The revenue incident agent keeps these in its project plan and impact estimate |
| Design decisions, in short | RFQ-to-quote, AML alert investigator. Every project has the full reasoning in its decision log |
| Sections on the subject itself | Operations in RFQ-to-quote; model routing, a second model vendor and budget control in the AML alert investigator; what is watched, detection and investigation in the revenue incident agent |

## Three diagrams

| File | What it shows | Where it sits in the README | Rendered by |
|---|---|---|---|
| `docs/request-to-system.png` | Four columns: the request in the client's words, the user stories, the part of the system that answers each story, and the number from the evaluation that proves it | End of "The problem" | `scripts/render_request_to_system.py` |
| `docs/agent-flow.png` | The components and which of them call a model | Top of the README or of "How it works" | `scripts/render_flow.py` |
| `docs/agent-flowchart.png` | A flowchart for one request: start and end, each step, each decision with its yes and no branch, and every exit to a person with its reason | "Decision flow" | `scripts/render_flowchart.py` |

The scripts stay in the repository so a diagram is regenerated when the system changes.

## Delivery documents

| File | Content |
|---|---|
| `docs/discovery-notes.md` | The request in the client's words, how the work is done today, what was observed, user stories, success criteria, what is out of scope, open questions |
| `docs/workflow-map.md` | The workflow today and with the system in it, and what changes for the people doing the work |
| `docs/project-plan.md` | Goal, people, timeline, go-live gates, risks |
| `docs/decision-log.md` | Each design decision, the alternative that was considered and the reason |
| `docs/impact-estimate.md` | The assumptions behind the estimate and how the result should be read |
| `docs/eval-report.md` | Written by the evaluation command, never by hand |

## Evaluation

- A labelled set is committed, or rebuilt from a seed by one command.
- A baseline that uses no model is scored on the same set, so the model has to earn
  its cost.
- Part of the set is held back from tuning, and the README says which part.
- One command scores every configuration and writes `docs/eval-report.md`.
- The worst error is named and counted on its own line: a quote approved that a person
  should have seen, a suspicious alert closed, an unsafe mitigation proposed.
- Headline results on small sets are given as counts.
- A paragraph says how far the numbers can be trusted.

## Running without a key

Anyone can clone the repository and run a project without a key. The baseline needs no
model, and model configurations replay recorded responses from `data/`. CI uses this
mode and holds no secret.

## Models

- A larger model has to be justified by numbers against a cheaper one, and where it
  was not, the README says so.
- The provider sits behind one interface.
- Cost per case is reported next to the result.

## Tests and CI

- `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .` pass.
- The project has its own job in `.github/workflows/ci.yml` that runs lint, the tests
  and the evaluation without a key.
- The project has a row in the [root README](../README.md). A project whose build
  stopped before its recorded model run keeps a `HANDOVER.md` naming what is left, and
  gets its row when that file is gone.
- CI also runs `scripts/check_pages.py` on the root README, the pages in `docs/` and
  every project README. It fails on a dead link or anchor, and on a figure the root
  README quotes that a project README no longer states.

## Live projects

A project that runs on a live public feed meets everything above and the points below.
The [tender radar](../projects/tender-radar/) is the reference.

- **One code path for history and for today.** The backtest replays stored history, in
  date order, through the function the daily run calls. There is no separate
  evaluation pipeline.
- **A prediction log.** Append-only. Each row holds the item, the time the prediction
  was written, the system version and the prediction. A row is never rewritten, and a
  rerun adds nothing.
- **A system version.** A hash of the configuration, the prompts and the model name. A
  changed prompt is a new version with its own line on the scoreboard.
- **An outcome log.** Outcomes are collected by a separate command and stored with the
  day they arrived. A live prediction is scored only if it was written before its
  outcome arrived. Backtest numbers and live numbers are never added together.
- **A scoreboard.** One JSON file and one static page: live since, predictions scored
  and still waiting, the headline number next to the baseline, the worst error as a
  count, cost per day, the last run and the feed's health.
- **A schedule.** A workflow with a cron trigger and a manual trigger. Steps that need
  a paid model are skipped without the secret. The run writes to a `live-data` branch,
  never to `main`.
- **A spend cap in code**, checked before every model call. At the cap the run goes on
  without the model and counts what it skipped.
- **Feed health checks.** A response that no longer parses, a stale feed or a day with
  far fewer items than usual fails the run. It is never recorded as a quiet day.
- **Read-only on the outside world**, with paced requests and a generic client name.
- **Committed data.** Ids, labels, predictions and scores. Third-party text only where
  its licence allows, with a `NOTICE` file. No names, email addresses or contact
  fields. CI never calls the feed.
- **Operations.** A `Dockerfile`, a compose file and `docs/runbook.md`.

## What is said plainly

- The client is fictional, and the README says so.
- Synthetic data is called synthetic.
- A path that was never run is labelled as never run.
