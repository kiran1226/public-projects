# Workflow map

> Simulated engagement with a fictional maintainer team. Minutes are assumptions used
> for the impact estimate, not measurements.

## Before

| # | Step | Who | Minutes per issue | Pain |
|---|---|---|---:|---|
| 1 | Notice the new issue | Whoever is on the rota | 0 | It waits until someone looks: minutes in office hours, a night otherwise |
| 2 | Read it | Triager | 1.5 | Reports vary from one line to several screens |
| 3 | Choose area and platform labels | Triager | 0.5 | No written definition of a label; two people label the same issue differently |
| 4 | Search for an earlier report | Triager | 2 | Keyword search on the title. A report in other words is not found |
| 5 | Comment and close as a duplicate, or leave open | Triager | 0.5 | The original is named in free text |
| | **Total per issue** | | **4.5** | |

## After

| # | Step | Who | Minutes per issue | What changed |
|---|---|---|---:|---|
| 1 | Fetch issues opened since the last run | Desk (code) | 0 | Hourly; an issue is stored as first seen |
| 2 | Search earlier issues by words and by meaning | Desk (code and a local embedding model) | 0 | Finds a report written in other words |
| 3 | Score every label | Desk (classical classifier) | 0 | A second opinion that costs nothing |
| 4 | Read the issue, the label definitions and five earlier issues | Desk (LLM) | 0 | Proposes labels with a confidence, and a duplicate or none |
| 5 | Gate | Desk (code) | 0 | A label must be in the taxonomy; a duplicate must be one of the issues retrieved and older |
| 6 | Record the proposal | Desk (code) | 0 | Shadow mode: nothing is written to the tracker |
| 7 | Open the queue, confirm or correct | Triager | 1.5 | Starts from a proposal; the matching passage of each earlier issue is on the page |
| 8 | Close as a duplicate, or leave open | Triager | 0.5 | Still a person's decision |
| | **Total per issue** | | **2** | |

## Who decides what

| Decision | Before | After |
|---|---|---|
| Which labels exist | The tracker's label list | The same list, limited to labels with enough history to learn and score |
| Which labels an issue gets | Triager | The desk proposes; a triager confirms. In apply mode, labels at high confidence are added without waiting |
| Whether an issue is a duplicate | Triager, after a keyword search | The desk proposes with the earlier issue shown; a triager decides |
| Whether to close | Triager | Triager. The desk never closes an issue |
| Whether the desk may write to a tracker | n/a | The configuration's allow-list, empty by default |

## What changes for the people

- Triagers stop searching and start checking. The risk that comes with that is
  agreeing too easily; the queue shows the desk's confidence and why a person was
  asked, and every correction is kept.
- The support lead gets a scoreboard: how often the desk agreed with the team on
  issues it had not seen, and what it cost.
- Developers see fewer issues with a wrong area label, if the measured error rate of
  labels applied alone holds.
