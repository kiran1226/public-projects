# Decision log

Choices made while building the desk, with the alternative that was considered and
the trade-off accepted. Numbers come from the development months (issues opened May
to August 2026) unless a line says otherwise. The
[evaluation report](eval-report.md) has the full tables.

## 1. Score against what the maintainers did, not against labels written for the project

**Decision.** The reference for every number is the tracker itself: the labels on an
issue and its close reason, read at least fourteen days after it was opened.

**Alternative.** A hand-labelled set of 40 to 60 issues, as in the earlier projects in
this repository.

**Why.** The maintainers' record is free, public, and was not made with this system
in mind. It gives 2,243 scored issues instead of 60, and it keeps arriving, so the
live log can be scored the same way.

**Trade-off.** The reference is not the truth. Maintainers leave some issues
unlabelled, label unevenly and miss duplicates, and each of those counts against the
desk. The report says so next to every table.

## 2. A split in time, six months wide

**Decision.** Training and the taxonomy use issues opened before 1 May 2026. The
test window is May to October 2026. September and October were not looked at until
every setting below was fixed.

**Alternative.** The three-month window the brief planned.

**Why.** Duplicates are rare. Only 123 issues opened May to October were closed as
duplicates, and 12 of those in October. Three months would have left a held-out part
with about a dozen duplicates, too few to say anything.

**Trade-off.** The classifier is six months stale at the end of the window. That is
how a deployed system ages, so it is left in.

## 3. No GitHub token: history through search, timelines only for duplicates

**Decision.** The desk works without a token. Issues come from the search endpoint
(100 per request, 10 requests a minute without a token), in windows of creation time
because a search returns at most 1,000 results. A timeline costs one request against
60 an hour, so timelines are fetched only for issues closed as duplicates. A token in
`GITHUB_TOKEN` raises the limits and is never required.

**Alternative.** Require a token, or read a public archive of GitHub events.

**Why.** Measured before any code was written: two thirds of each page of the issue
list endpoint were pull requests, the search endpoint returns issues only, and the
whole history since 2022 (33,000 issues) came down in about 45 minutes. A reviewer
can rebuild everything with no account.

**Trade-off.** Two things a token would have bought were given up. Who applied each
label (a person or a bot) is known only for the issues whose timeline was read. And
the backtest uses each issue's title as it is today: taking the title from the first
rename event, as the experiment before the build suggested, needs every timeline.

## 4. History from 2022, not the whole tracker

**Decision.** Issues opened from 1 January 2022 are stored: 33,000 of the tracker's
64,000.

**Why.** Older issues describe an engine two major versions back. Fetching and
embedding scale with the count.

**Trade-off.** A duplicate of an issue opened before 2022 cannot be found, and its
original counts as "unresolved". The evaluation report counts those.

## 5. The taxonomy is built from history, not from the tracker's label descriptions

**Decision.** A label's definition is the words that mark it in the training issues
and two example titles, computed by `taxonomy.py`. A label is kept only if at least
30 training issues carry it.

**Alternative.** Use the repository's own label descriptions, as the brief planned.

**Why.** The Godot tracker's area and platform labels have no description. Building
definitions from history also means `init` works on a repository nobody has
documented.

**Trade-off.** The definitions describe how the label was used, including habits
that are wrong. Four labels were too rare to keep and are never proposed.

## 6. "Kind" is not predicted

**Decision.** The desk proposes area (`topic:*`) and platform (`platform:*`) labels.
It does not propose `bug`, `enhancement` or `discussion`.

**Why.** In 2,000 closed issues read before the build, `bug` was on 1,373 and no
other kind on more than 75. Feature requests live in a separate repository. A label
that is nearly constant teaches nothing and inflates every score.

## 7. Labels are scored as sets

**Decision.** Precision and recall are counted over labels, not issues. "Area right"
means every area the desk proposed is on the issue and it proposed at least one.

**Why.** In the test window 732 of 2,243 issues carry several area labels and 295
carry none. A score that assumes one label per issue would drop half the tracker,
which is what the experiment before the build had to do.

**Trade-off.** A label proposed on an issue the maintainers left unlabelled counts as
wrong, although nobody knows what the label would have been. The report shows those
apart.

## 8. Issue text is never committed

**Decision.** The committed logs hold issue numbers, label names, the desk's answers
and the outcomes. Issue text, the local store, model recordings on real issues and
the model's one-sentence note stay under `var/`.

**Alternative.** Commit the recordings, as the other projects do, so that anyone can
replay the model runs.

**Why.** GitHub's terms leave content with its authors and license it for use
through the service. The evaluation needs no text, so nothing is lost for a reader.

**Trade-off.** Replaying the model on the real tracker needs a key and costs about
two US dollars. The demo and the tests run on an invented tracker instead, whose
recordings are committed.

## 9. The matching passage is computed, not quoted by the model

**Decision.** For each earlier issue shown to a triager, the passage is the sentence
of that issue sharing the most words with the new one, found by code.

**Alternative.** Ask the model to quote the passage and check the quote, as the
tender radar does for conditions.

**Why.** The passage only has to help a person compare two reports. Computing it
makes it a real sentence of the issue every time, costs nothing, and keeps the
model's output to a few dozen tokens.

**Trade-off.** The sentence with the most shared words is not always the one that
shows the two reports are the same.

## 10. The original of a duplicate comes from three rules, and "unresolved" is allowed

**Decision.** The tracker has no field for "duplicate of". `truth.py` reads the
timeline and takes, in order: the last comment before the close that speaks of a
duplicate and names an older issue; a comment within half an hour before the close
naming exactly one older issue; exactly one older issue named anywhere in the
discussion. Otherwise the outcome is "outside" (another repository) or "unresolved",
and the issue is left out of the duplicate scores.

**Why.** The first rule alone resolved 46 of the first 58 timelines read. The other
two brought it to 50, and the remaining 8 were 4 pointing to another repository and
4 naming several issues with nothing to choose between them. Guessing there would
put the desk's own judgement into the reference. Over all 123 duplicates of the test
window the rules resolved 101; 9 point to another repository and 13 stay unresolved.

## 11. A proposal that names any issue of the same duplicate family is right

**Decision.** Resolved duplicates are joined into families. Naming the second report
when the maintainers closed against the first counts as found.

**Why.** Maintainers close a third report against whichever earlier one they
remember. Both are the same problem.

## 12. One model call per issue, and the state of earlier issues is not shown

**Decision.** Labels and the duplicate question are answered in one call. The five
earlier issues are shown with number, opening date, title and the start of the
description, without their state.

**Why.** One call halves the cost. The state is left out because in a replay it
would be today's state: "closed as a duplicate" on a candidate is information the
desk could not have had on the day.

## 13. A duplicate must be one of the issues retrieved

**Decision.** The gate drops a duplicate that is not among the five issues shown to
the model.

**Why.** It is the one check that makes a duplicate claim inspectable: the triager
sees the same five. It also means text in an issue cannot make the desk point a
reporter at an arbitrary issue number.

**Trade-off.** If the original is not in the first five, the desk cannot find it.
The evaluation report gives that share.

## 14. The embedding model reads the first 600 characters

**Decision.** Title plus the start of the problem description, cut at 600
characters, embedded with a small English model on the CPU.

**Why.** Measured on a laptop: 12 issues a second at 1,200 characters, 33 at 600.
For 33,000 issues that is 45 minutes against 17.

**Trade-off.** A report whose telling detail is far down the body is matched on its
opening only. Word search reads 1,500 characters and partly makes up for it.

## 15. Two searches fused with a small constant, and eight issues shown to the model

**Decision.** Word search and vector search each return 50 earlier issues. They are
fused by rank with a constant of 10, and the model reads the first eight.

**Alternatives.** Either search alone; the usual fusion constant of 60; five issues,
as the brief planned; a local cross-encoder to rerank the fused list.

**Why.** Counted on the 73 development duplicates whose original is known, the
original was among the first results this often:

| Search | First 5 | First 8 | First 20 |
|---|---:|---:|---:|
| Words alone | 30 | 32 | 40 |
| Vectors alone | 33 | 39 | 46 |
| Fused, constant 60 (an earlier check) | 33 | 39 | 51 |
| Fused, constant 10 | 36 | 45 | 51 |

The two searches find different originals, so fusing them helps most further down
the list. A small constant keeps each search's first few results near the top. Going
from five issues to eight puts the original in front of the model for 45 duplicates
instead of 36, for about a third more input tokens. A cross-encoder reranker
(`ms-marco-MiniLM-L-6-v2`), tried on the first 50 duplicates, placed 21 in the first
five against 25 without it, at 0.6 seconds an issue. It was dropped.

**Trade-off.** For 28 of 73 duplicates the original is not among the eight, and the
desk cannot find those. Reading the misses shows why: they are reports of one root
cause with different symptoms. One example: a report that rotating a body changes
its scale was closed against an older report that a function returning the scale is
affected by rotation. A maintainer knows the two are one defect; text similarity
does not.

## 16. No similarity threshold makes a usable duplicate detector

**Decision.** The classifier configuration says "duplicate" when the most similar
earlier issue reaches a cosine similarity of 0.90, and "sure" at 0.95. Both values
are kept only so that the baseline has a duplicate answer at all.

**Why.** On the 1,489 development issues:

| Threshold | Issues flagged | Right | New issues flagged |
|---:|---:|---:|---:|
| 0.86 | 370 | 8 | 338 |
| 0.90 | 78 | 4 | 68 |
| 0.93 | 16 | 1 | 15 |
| 0.95 | 8 | 0 | 8 |

Issues about the same part of one product are all close to one another in
embedding space. Similarity ranks candidates well enough to be useful and decides
nothing. The duplicate decision is the part of this system that needs a model.

## 17. The classifier's "confident" is the best it can do, not 19 in 20

**Decision.** The classifier proposes a label at a probability of 0.55 and calls it
confident at 0.99. Both come from the newest tenth of the training issues, which the
model was not fitted on.

**Why.** The plan was to call a label confident at the lowest probability where 19
of 20 proposals are right. No probability gets there. On the 3,093 validation issues
the proposals at 0.99 were right 546 times in 645 (85%). An unweighted variant,
tried in an earlier check with part of the history, reached 92% on the development
months only by proposing 196 labels, a tenth of those present. So the threshold with the best precision is used, and the validation record
in `data/log/backtest-meta.json` says the target was not met.

**What it shows.** The maintainers' labels are uneven enough that a word model
cannot agree with them 19 times in 20, whatever its threshold. Any success criterion
for "labels applied alone" has to be read against that.

## 18. The classifier is a second opinion on the model's labels

**Decision.** A label the model gives at high confidence keeps that confidence only
if the classifier proposes the label too. A label the model gives at medium
confidence without the classifier's agreement is not proposed. The classifier can
lower a confidence; it cannot raise one or add a label.

**Alternative.** Take the model's confidence as it comes.

**Why.** Measured on the model's first run over the 322 issues of May 2026:

| The model said | The classifier | Area labels right | Platform labels right |
|---|---|---:|---:|
| high | proposes it too | 153 of 169 (91%) | 27 of 34 (79%) |
| high | does not | 23 of 32 (72%) | 1 of 4 |
| medium | proposes it too | 76 of 108 (70%) | 12 of 18 |
| medium | does not | 19 of 60 (32%) | 2 of 11 |

Two readers that work differently and agree are right more often than either. The
rule is free: both answers are already there.

**Trade-off.** Recall falls, because some labels only the model saw are no longer
proposed. On May, between the first run and the run with this rule (which also had
the revised duplicate instruction of entry 19), labels right of proposed went from
313 of 436 (72%) to 285 of 357 (80%), and the share of the maintainers' labels found
from 69% to 63%. For a first pass that sends issues to the wrong people when it is
wrong, precision was chosen.

## 19. The duplicate instruction was rewritten once, on May

**Decision.** The instruction says when to answer "no duplicate": an earlier issue
that is only related, a fixed problem that has come back, the same error message in
another situation. A duplicate the model gives at low confidence is not a proposal.

**Why.** With the first wording the model named an earlier issue for 121 of the 322
issues of May, 67 of them at low confidence: it was naming the closest issue and
using the confidence to say it probably was not one. After the change it named one
for 40.

**How it was done.** One revision, judged on May only, then the whole of the
development months was run with it. The first wording's rows stay in the prediction
log as their own system version; a row is never removed. The held-out months were
run once, after this.

## 20. "Closed as a duplicate" is a narrow reference, and the report says so

**Decision.** The duplicate scores are reported against the maintainers' close
reason, as planned, with a separate review of what that reference misses
([duplicate-review.md](duplicate-review.md)).

**Why.** On the development months the desk called 21 issues duplicates at high
confidence and the record confirms 3. Reading the other 18 beside the issue named,
16 are the same defect: several are the same report filed twice and then closed as
"completed" or left open. In a random sample of 20 medium-confidence calls the
record does not confirm, 14 are the same defect. The reading is the AI's, not a
maintainer's, and it is not used in any score.

**Consequence.** "A new issue called a duplicate" is kept as the worst error and
counted on its own line, but it is an upper bound and a loose one. The honest
summary of the duplicate step is: it agrees with the maintainers' close reason on a
minority of the duplicates they marked, and most of its other calls point at a real
earlier report of the same defect that nobody marked.

## 21. The cheapest model, and no larger one

**Decision.** Claude Haiku 5.5 at low effort, one call per issue.

**Why.** An experiment before the build compared three model sizes on 400 issues of
this tracker for the area label: 335 right for Haiku (334 to 338 over three runs),
336 for Sonnet, 342 for Opus, at 20 and 40 times the cost. The differences are
inside Haiku's own run-to-run spread.

**Not measured.** Whether a larger model judges duplicates better. That is the
place a larger model might earn its cost, and the reference is too narrow to tell
(entry 20).

## 22. The cascade is kept as a configuration and not used live

**Decision.** `cascade` lets the classifier answer alone when it is confident of an
area, unsure of nothing, and sees no close earlier issue. The live run uses `model`.

**Why.** The classifier is rarely that sure (entry 17), so the cascade asks the
model for almost every issue and its scores are the model's. At under one US dollar
per 1,000 issues there is little to save. The evaluation report has both lines.

## 23. An hourly schedule, and a flag for answers that came too late

**Decision.** The live run is hourly. Each row records whether the tracker already
showed a label of the taxonomy, or a close, when the desk first saw the issue, and
the live scores leave those rows out.

**Why.** Maintainers label fast: a median of 1.4 hours after an issue opens. A daily
run would answer almost every issue after its answer was on the page. An hourly run
still loses many; the flag makes sure a prediction is only scored if it was one.

**Trade-off.** Fewer scored live predictions than issues answered. The scoreboard
shows both counts. A webhook would answer within seconds and was out of scope.

## 24. Shadow by default; apply mode writes little and has never been run

**Decision.** Without `--apply` the desk writes its own log. With it, and only for
a repository in `apply_allow`, it adds labels at high confidence and posts at most
one comment for a duplicate at high confidence. It has no code that closes an issue
or removes a label. An answer the gate had to change is not acted on. The quoted
passage in a comment has its "@" removed so that it cannot notify anyone.

**Why.** The client asked to know how often the desk is wrong before it touches the
tracker. The numbers so far (entry 17, and the labels applied alone in the
evaluation report) do not support switching it on.

**Status.** The write path has tests against a fake tracker. It has never been run
against GitHub.

## 25. The live desk is trained to a fixed day

**Decision.** `live_cut` in the configuration is the day the live classifier and
taxonomy are trained up to. Moving it forward retrains both and changes the system
version, so the scoreboard starts a new line.

**Alternative.** Retrain on every run.

**Why.** A system that changes every hour has no version to score. Freezing it makes
"issues that arrived after the system was frozen" literally true of the live log.

## 26. The label definitions stay in the prompt, although they did not help

**Decision.** The desk that runs live is the one that was evaluated: the model with
label definitions and eight earlier issues. Nothing was changed after the held-out
months were scored.

**What was found.** The model given only the label names got the area right on
more issues than the desk: 443 against 412 to 419 of 638 on the held-out months, and
928 against 890 of 1,310 on the development months. The desk's labels were more
often on the issue (69% against 66% held out) and it found fewer of the
maintainers' labels (61% against 67%). The definitions, built from telling words
and example titles, are 9,500 of the prompt's characters, roughly 3,500 of the 8,150
input tokens of a call, and bought nothing measurable.

**Why it was not acted on.** The "model alone" configuration was added to show what
the model knows without help, and was run on the development months only after the
held-out result was in. Dropping the definitions now would be choosing a setting on
the held-out months. It is the first change to make, and to score on issues opened
after today.

**What else it suggests.** The earlier issues in the prompt may pull a label
towards the candidates' areas. That was not measured apart from the definitions.

## 27. What was not tuned, and what was not run

- The held-out months were run once per configuration (the desk three times, as
  repeat runs), after every setting above was fixed.
- The model's instructions for labels were never revised. The duplicate instruction
  was revised once (entry 19).
- No larger model, no second repository, no webhook. The second repository was the
  first item on the brief's cut list.
- The reranker was tried on development data and dropped (entry 15).
